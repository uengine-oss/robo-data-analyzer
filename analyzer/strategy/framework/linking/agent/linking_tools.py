"""Linking 에이전트 도구 세트 — 인덱스 조회 / 코드 검색 / 그래프 관계 등록 / DDL 조회.

호출자 (entity_linker, unresolved_linker) 가 ToolResultCollector 와 함께 호출.
LLM 도구 호출 결과를 collector 에 누적 → 호출자가 snapshot_and_clear 로 회수.

도구 11종 (모두 같은 에이전트의 단일 도구 세트 — 단일 처리 흐름):
  인덱스 조회: sql_lookup_tool / config_lookup_tool / artifact_lookup_tool
  ChunkBundle 검색: bundle_search_tool / bundle_get_chunk_tool
  그래프 등록: graph_create_refer_to_tool / graph_create_table_access_tool /
              graph_create_column_tool / graph_create_fk_tool
  DDL 조회: table_search_tool / table_columns_tool
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool as lc_tool

from analyzer.strategy.framework.analysis.chunk_bundle import (
    bundle_get_chunk as _bundle_get_chunk,
    bundle_search as _bundle_search,
)
from analyzer.strategy.framework.common.models.node_models import (
    ArtifactChunk,
    ColumnRef,
    ConfigSectionChunk,
    FkEdge,
    FunctionTableEdge,
    ReferToEdge,
    SqlStatementChunk,
)
from analyzer.strategy.framework.linking.table_linker import sql_op_to_method_relation
from config.settings import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 결과 수집기 — 도구 호출 결과를 스레드-세이프하게 누적
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ToolResultCollector:
    """도구 호출 결과를 스레드-세이프하게 수집한다."""
    refer_to: List[ReferToEdge] = field(default_factory=list)
    table_access: List[FunctionTableEdge] = field(default_factory=list)
    fk: List[FkEdge] = field(default_factory=list)
    columns: List[ColumnRef] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot_and_clear(self) -> Dict[str, list]:
        with self.lock:
            result = {
                "refer_to": list(self.refer_to),
                "table_access": list(self.table_access),
                "fk": list(self.fk),
                "columns": list(self.columns),
            }
            self.refer_to.clear()
            self.table_access.clear()
            self.fk.clear()
            self.columns.clear()
        return result

    @property
    def total_count(self) -> int:
        return len(self.refer_to) + len(self.table_access) + len(self.fk) + len(self.columns)


# ═══════════════════════════════════════════════════════════════════════════
# 인덱스 조회 헬퍼 — 외부에서도 직접 호출 (build_tools 외 경로)
# ═══════════════════════════════════════════════════════════════════════════


def sql_lookup(sql_key: str, sql_index: Dict[str, SqlStatementChunk]) -> Optional[Dict[str, Any]]:
    """sql_key 로 SqlStatementChunk → dict."""
    stmt = sql_index.get(sql_key)
    if not stmt:
        return None
    return {
        "sql_key": stmt.sql_key,
        "sql_type": stmt.sql_type,
        "sql_text": stmt.sql_text,
        "file_path": stmt.file_path,
        "start_line": stmt.start_line,
        "end_line": stmt.end_line,
    }


def config_lookup(config_key: str, config_index: Dict[str, ConfigSectionChunk]) -> Optional[Dict[str, Any]]:
    """config_key 로 ConfigSectionChunk → dict."""
    cfg = config_index.get(config_key)
    if not cfg:
        return None
    return {
        "config_key_path": cfg.config_key_path,
        "value": cfg.raw_snippet,
        "file_path": cfg.file_path,
        "start_line": cfg.start_line,
        "end_line": cfg.end_line,
    }


def build_artifact_lookup_index(artifact_index: Dict[str, ArtifactChunk]) -> Dict[str, str]:
    """artifact_index 의 chunk_id + hint_key 양방향 조회 인덱스 구성."""
    idx: Dict[str, str] = {}
    for cid, ac in (artifact_index or {}).items():
        cid_norm = (cid or "").strip()
        if cid_norm:
            idx.setdefault(cid_norm, cid_norm)
        hint_norm = (ac.hint_key or "").strip()
        if hint_norm:
            idx.setdefault(hint_norm, cid_norm)
    return idx


def _enrich_with_modulename(result: Dict[str, Any]) -> Dict[str, Any]:
    """filePath 에서 파일명(확장자 제외) → moduleName 추출."""
    fp = result.get("filePath", "")
    if fp:
        fname = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        result["moduleName"] = fname.rsplit(".", 1)[0] if "." in fname else fname
    return result


# ═══════════════════════════════════════════════════════════════════════════
# 메인 빌더 — 11종 도구 전체 세트 반환
# ═══════════════════════════════════════════════════════════════════════════


def build_tools(
    state: Dict[str, Any],
    collector: ToolResultCollector,
    function_id: str = "",
    variable_id: Optional[str] = None,
    batch_startpoint_tables: Optional[set] = None,
) -> List[Any]:
    """linking 에이전트용 도구 11종 전체 세트 반환.

    모든 링킹 에이전트는 동일 도구 세트 사용. 상황별 도구 선택은 SKILL.md 본문 지침에 따라 LLM 이 판단.
    """
    default_confidence = settings.deep_agent.default_confidence

    sql_index: Dict[str, SqlStatementChunk] = state.get("sql_index") or {}
    config_index: Dict[str, ConfigSectionChunk] = state.get("config_index") or {}
    artifact_index: Dict[str, ArtifactChunk] = state.get("artifact_index") or {}
    artifact_key_index = build_artifact_lookup_index(artifact_index)
    ddl_meta = state.get("ddl_table_metadata") or {}
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    data_dir = state.get("data_dir") or ""

    # 기존 FK 세트 (중복 생성 방지)
    existing_fk_set: set = set()
    for (_s, _n), info in ddl_meta.items():
        for fk in (info.get("foreign_keys") or info.get("foreignKeys") or []):
            fk_col = (fk.get("column") or "").strip().upper()
            fk_ref = (fk.get("ref") or "").strip().upper()
            if fk_col and fk_ref:
                existing_fk_set.add((_n.upper(), fk_col, fk_ref))

    lock = collector.lock

    # ─────────────────────────────────────────────────────────────────
    # 인덱스 조회 도구 (sql / config / artifact)
    # ─────────────────────────────────────────────────────────────────

    @lc_tool
    def sql_lookup_tool(sql_key: str) -> str:
        """sql_key로 SQL 문장을 조회한다."""
        r = sql_lookup(sql_key, sql_index)
        logger.info("[TOOL] sql_lookup | key=%s found=%s", sql_key, bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @lc_tool
    def config_lookup_tool(config_key: str) -> str:
        """config_key로 설정 엔트리를 조회한다."""
        r = config_lookup(config_key, config_index)
        logger.info("[TOOL] config_lookup | key=%s found=%s", config_key, bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @lc_tool
    def artifact_lookup_tool(key: str) -> str:
        """chunk_id 또는 hint_key(ejb-name)로 ArtifactChunk를 조회한다."""
        cid = artifact_key_index.get((key or "").strip())
        if not cid:
            return ""
        ac = artifact_index.get(cid)
        if not ac:
            return ""
        data = {
            "chunk_id": cid,
            "chunk_type": ac.chunk_type,
            "hint_key": ac.hint_key,
            "file_path": ac.source_path,
            "text": (ac.text or "")[:2000],
        }
        logger.info("[TOOL] artifact_lookup | key=%s chunk_id=%s", key, cid)
        return json.dumps(data, ensure_ascii=False)

    # ─────────────────────────────────────────────────────────────────
    # ChunkBundle 검색/조회 도구
    # ─────────────────────────────────────────────────────────────────

    @lc_tool
    def bundle_search_tool(query: str, kind: str = "symbol") -> str:
        """ChunkBundle에서 심볼/텍스트 검색한다. kind: symbol|text."""
        results = _bundle_search(data_dir, query, kind, span_index=span_index, chunk_index=chunk_index)
        enriched = [_enrich_with_modulename(r) for r in results[:20]]
        logger.info("[TOOL] bundle_search | query=%s kind=%s results=%d", query, kind, len(enriched))
        return json.dumps(enriched, ensure_ascii=False)

    @lc_tool
    def bundle_get_chunk_tool(chunk_id: str) -> str:
        """chunk_id로 코드 조각을 조회한다. 3000자 초과시 truncate (context 보호)."""
        r = _bundle_get_chunk(chunk_id, chunk_index=chunk_index)
        if r:
            _enrich_with_modulename(r)
            text = r.get("text") or ""
            if len(text) > 3000:
                r["text"] = text[:3000] + f"\n...[truncated, full length={len(text)} chars]"
                r["truncated"] = True
        logger.info("[TOOL] bundle_get_chunk | chunk_id=%s found=%s", chunk_id, bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    # ─────────────────────────────────────────────────────────────────
    # 그래프 관계 등록 도구 (REFER_TO / READS-WRITES / Column / FK)
    # ─────────────────────────────────────────────────────────────────

    @lc_tool
    def graph_create_refer_to_tool(
        target_type: str, target_key: str,
        evidence: str = "", source_function_id: str = "",
    ) -> str:
        """REFER_TO/CALLS_SQL/USES_CONFIG 엣지를 생성한다. source_function_id 필수."""
        mid = source_function_id.strip() if source_function_id else function_id
        if not mid:
            return "error: source_function_id required"
        with lock:
            collector.refer_to.append(ReferToEdge(
                function_id=mid,
                target_type=target_type,
                target_id=target_key,
                evidence={"note": evidence} if evidence else {},
                confidence=default_confidence,
                variable_id=variable_id,
            ))
        logger.info("[TOOL] refer_to | %s → %s:%s", mid, target_type, target_key)
        return "ok"

    @lc_tool
    def graph_create_table_access_tool(
        table_name: str, access: str,
        evidence: str = "", source_function_id: str = "",
    ) -> str:
        """READS/WRITES 엣지를 생성한다. access: READS|WRITES. source_function_id 필수."""
        mid = source_function_id.strip() if source_function_id else function_id
        rel = access.upper() if access else "READS"
        if rel not in ("READS", "WRITES"):
            rel = "READS"
        if not mid:
            return "error: source_function_id required"
        with lock:
            collector.table_access.append(FunctionTableEdge(
                function_id=mid,
                table_schema="",
                table_name=table_name,
                relation=sql_op_to_method_relation("INSERT" if rel == "WRITES" else "SELECT"),
                variable_id=variable_id,
            ))
        logger.info("[TOOL] table_access | %s → %s %s", mid, rel, table_name)
        return "ok"

    @lc_tool
    def graph_create_column_tool(
        table_name: str, column_name: str,
        dtype: str = "", description: str = "",
    ) -> str:
        """Table 의 Column 을 등록한다. (Table)-[:HAS_COLUMN]->(Column).

        코드/구조체/DTO 정의에서 실제 관찰된 필드만 등록. 추측 금지.
        description 은 해당 함수에서 이 컬럼을 왜/어떻게 쓰는지 1~2 문장. 모르면 빈 문자열.
        dtype 도 관찰된 경우만 (C struct 필드 타입 / DTO 필드 타입). 모르면 빈 문자열.
        """
        tbl = (table_name or "").strip()
        col = (column_name or "").strip()
        if not tbl or not col:
            return "error: table_name and column_name required"
        with lock:
            collector.columns.append(ColumnRef(
                table_schema="",
                table_name=tbl,
                column_name=col,
                dtype=(dtype or "").strip() or None,
                description=(description or "").strip() or None,
            ))
        logger.info("[TOOL] column | %s.%s%s", tbl, col, f" ({dtype})" if dtype else "")
        return "ok"

    @lc_tool
    def graph_create_fk_tool(
        from_table: str, from_column: str,
        to_table: str, to_column: str,
        evidence: str = "", from_schema: str = "", to_schema: str = "",
    ) -> str:
        """FK 관계를 등록한다. from_table=FK쪽(자식), to_table=PK쪽(부모)."""
        if from_table.upper() == to_table.upper():
            return "error: self-referencing FK not allowed"
        if batch_startpoint_tables:
            if from_table.upper() not in {t.upper() for t in batch_startpoint_tables}:
                return "skipped: from_table not in this batch's startpoints"
        if ddl_meta:
            known_tables = {n.upper() for (_, n) in ddl_meta.keys()}
            for tbl in (from_table, to_table):
                if tbl.upper() not in known_tables:
                    return f"error: '{tbl}' not found in DDL table list"
        ref_key = f"{to_table}.{to_column}".upper()
        check_key = (from_table.upper(), from_column.upper(), ref_key)
        if check_key in existing_fk_set:
            return "already exists (from DDL)"
        existing_fk_set.add(check_key)
        with lock:
            collector.fk.append(FkEdge(
                from_schema=from_schema, from_table=from_table, from_column=from_column,
                to_schema=to_schema, to_table=to_table, to_column=to_column,
                candidate=True,
            ))
        logger.info("[TOOL] fk | %s.%s → %s.%s", from_table, from_column, to_table, to_column)
        return "ok"

    # ─────────────────────────────────────────────────────────────────
    # DDL 메타 검색/조회 도구
    # ─────────────────────────────────────────────────────────────────

    @lc_tool
    def table_search_tool(keyword: str) -> str:
        """키워드로 DDL 테이블을 검색한다. 대소문자/언더스코어 무시."""
        keyword_norm = keyword.lower().replace("_", "")
        results = [
            {"schema": s, "name": n}
            for (s, n) in ddl_meta
            if keyword_norm in n.lower().replace("_", "")
        ]
        logger.info("[TOOL] table_search | keyword=%s results=%d", keyword, len(results))
        return json.dumps(results[:20], ensure_ascii=False) if results else "no matching tables found"

    @lc_tool
    def table_columns_tool(table_name: str, table_schema: str = "") -> str:
        """테이블의 컬럼 목록과 PK 정보를 조회한다."""
        for (s, n), info in ddl_meta.items():
            if n.lower() == table_name.lower() and (not table_schema or s.lower() == table_schema.lower()):
                columns = info.get("columns") or []
                pks = info.get("primary_keys") or []
                col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
                logger.info("[TOOL] table_columns | table=%s columns=%d", table_name, len(col_names))
                return json.dumps({"schema": s, "name": n, "primary_keys": pks, "columns": col_names}, ensure_ascii=False)
        return f"table '{table_name}' not found"

    return [
        sql_lookup_tool, config_lookup_tool, artifact_lookup_tool,
        bundle_search_tool, bundle_get_chunk_tool,
        graph_create_refer_to_tool, graph_create_table_access_tool,
        graph_create_column_tool, graph_create_fk_tool,
        table_search_tool, table_columns_tool,
    ]


__all__ = [
    "ToolResultCollector",
    "build_tools",
    "sql_lookup",
    "config_lookup",
    "build_artifact_lookup_index",
]
