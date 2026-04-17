"""DeepAgent 도구 팩토리 (Tool Factory).

LangChain @tool 데코레이터로 DeepAgent가 사용할 도구들을 생성한다.
각 도구는 호출 즉시 collector에 결과를 누적하며, phase_linking에서
Neo4j에 즉시 반영한다.

[도구 분류]
  1. 인덱스 조회 — SQL/Config/Artifact 메타데이터 검색
  2. 코드 검색   — ChunkBundle 기반 심볼/텍스트 검색
  3. 관계 등록   — REFER_TO, READS/WRITES, FK 엣지 생성
  4. DDL 조회    — 테이블/컬럼 메타데이터 검색

[Skill별 도구 세트]
  - entity-analysis:  코드 검색 + 관계 등록(table_access, fk) + DDL 조회(있을 때)
  - table-tracking:   코드 검색 + 관계 등록(table_access, fk) + DDL 조회
  - sql-tracking:     인덱스 조회(sql) + 코드 검색 + 관계 등록(refer_to, table_access, fk) + DDL 조회(있을 때)
  - config-tracking:  인덱스 조회(config) + 코드 검색 + 관계 등록(refer_to)
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from config.settings import settings
from analyzer.strategy.framework.common.models import (
    ArtifactChunk,
    ColumnRef,
    ConfigSectionChunk,
    FkEdge,
    FunctionTableEdge,
    ReferToEdge,
    SqlStatementChunk,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 1. 결과 수집기 (Collector)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ToolResultCollector:
    """도구 호출 결과를 스레드-세이프하게 수집한다.

    각 도구는 이 collector에 결과를 추가하고, phase_linking에서
    배치 단위로 꺼내어 Neo4j에 반영한다.
    """
    refer_to: List[ReferToEdge] = field(default_factory=list)
    table_access: List[FunctionTableEdge] = field(default_factory=list)
    fk: List[FkEdge] = field(default_factory=list)
    columns: List[ColumnRef] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot_and_clear(self) -> Dict[str, list]:
        """현재까지 수집된 결과를 반환하고 내부 리스트를 비운다.

        Neo4j 즉시 반영 시 호출: 결과를 꺼내고 → Neo4j에 쓰고 → 다음 배치.
        """
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
        """수집된 전체 엣지 수."""
        return len(self.refer_to) + len(self.table_access) + len(self.fk) + len(self.columns)


# ═══════════════════════════════════════════════════════════════════════════
# 2. 인덱스 조회 헬퍼 (순수 함수)
# ═══════════════════════════════════════════════════════════════════════════


def sql_lookup(sql_key: str, sql_index: Dict[str, SqlStatementChunk]) -> Optional[Dict[str, Any]]:
    """sql_key로 SQL 문장을 조회한다."""
    stmt = sql_index.get(sql_key)
    if not stmt:
        return None
    return {
        "sql_key": stmt.sql_key,
        "sql_type": stmt.sql_type,
        "sql_text": getattr(stmt, "sql_text", ""),
        "file_path": getattr(stmt, "file_path", ""),
        "start_line": getattr(stmt, "start_line", 0),
        "end_line": getattr(stmt, "end_line", 0),
    }


def config_lookup(config_key: str, config_index: Dict[str, ConfigSectionChunk]) -> Optional[Dict[str, Any]]:
    """config_key로 설정 엔트리를 조회한다."""
    cfg = config_index.get(config_key)
    if not cfg:
        return None
    return {
        "config_key_path": cfg.config_key_path,
        "value": getattr(cfg, "raw_snippet", "") or getattr(cfg, "value", ""),
        "file_path": getattr(cfg, "file_path", ""),
        "start_line": getattr(cfg, "start_line", 0),
        "end_line": getattr(cfg, "end_line", 0),
    }


def build_artifact_lookup_index(artifact_index: Dict[str, ArtifactChunk]) -> Dict[str, str]:
    """hint_key 또는 chunk_id로 ArtifactChunk를 찾기 위한 역인덱스를 생성한다."""
    idx: Dict[str, str] = {}
    for cid, ac in (artifact_index or {}).items():
        cid_norm = (cid or "").strip()
        if cid_norm:
            idx.setdefault(cid_norm, cid_norm)
        hint = getattr(ac, "hint_key", None)
        hint_norm = (hint or "").strip()
        if hint_norm:
            idx.setdefault(hint_norm, cid_norm)
    return idx


# ═══════════════════════════════════════════════════════════════════════════
# 3. 도구 팩토리 (메인 진입점)
# ═══════════════════════════════════════════════════════════════════════════


def build_tools(
    state: Dict[str, Any],
    collector: ToolResultCollector,
    function_id: str = "",
    variable_id: Optional[str] = None,
    skill_name: str = "",
) -> List[Any]:
    """Skill에 맞는 LangChain 호환 도구 목록을 생성한다.

    Args:
        state: pipeline state (인덱스, DDL 메타 등 포함).
        collector: 도구 결과 수집기 (스레드-세이프).
        function_id: 기본 소스 함수 ID (미매칭 키 추적 시 사용).
        variable_id: 기본 소스 변수 ID (변수 기반 참조 시 사용).
        skill_name: 활성화할 Skill 이름. 이에 따라 노출할 도구가 결정됨.
            - "entity-analysis": 코드 검색 + table_access + fk
            - "table-tracking":  코드 검색 + table_access + fk + DDL 조회
            - "sql-tracking":    SQL 조회 + 코드 검색 + refer_to + table_access + fk
            - "config-tracking": Config 조회 + 코드 검색 + refer_to
            - "" (빈 문자열): 모든 도구 (폴백)

    Returns:
        LangChain @tool 데코레이터가 적용된 도구 함수 리스트.
    """
    from langchain_core.tools import tool as lc_tool
    from analyzer.strategy.framework.common.utils import sql_op_to_method_relation

    default_confidence = settings.deep_agent.default_confidence
    lock = collector.lock

    # ── state에서 인덱스 참조 ──
    sql_index: Dict[str, SqlStatementChunk] = state.get("sql_index") or {}
    config_index: Dict[str, ConfigSectionChunk] = state.get("config_index") or {}
    artifact_index: Dict[str, ArtifactChunk] = state.get("artifact_index") or {}
    artifact_key_index = build_artifact_lookup_index(artifact_index)
    ddl_meta = state.get("ddl_table_metadata") or {}
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    data_dir = state.get("data_dir") or ""

    # ── 기존 FK 세트 (중복 생성 방지) ──
    existing_fk_set: set = set()
    for (_s, _n), info in ddl_meta.items():
        for fk in (info.get("foreign_keys") or info.get("foreignKeys") or []):
            fk_col = (fk.get("column") or "").strip().upper()
            fk_ref = (fk.get("ref") or "").strip().upper()
            if fk_col and fk_ref:
                existing_fk_set.add((_n.upper(), fk_col, fk_ref))

    from analyzer.strategy.framework.parsing.chunk_bundle import (
        bundle_search as _bundle_search,
        bundle_get_chunk as _bundle_get_chunk,
    )

    # ── 유틸: filePath → moduleName 추출 ──
    def _enrich_with_modulename(result: Dict[str, Any]) -> Dict[str, Any]:
        fp = result.get("filePath", "")
        if fp:
            fname = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            result["moduleName"] = fname.rsplit(".", 1)[0] if "." in fname else fname
        return result

    # ─────────────────────────────────────────────────────────────
    # 도구 정의 (각 도구는 @lc_tool 데코레이터 적용)
    # ─────────────────────────────────────────────────────────────

    # ── 인덱스 조회 도구 ──

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
            "chunk_type": getattr(ac, "chunk_type", ""),
            "hint_key": getattr(ac, "hint_key", ""),
            "file_path": getattr(ac, "source_path", ""),
            "text": getattr(ac, "text", "")[:2000],
        }
        logger.info("[TOOL] artifact_lookup | key=%s chunk_id=%s", key, cid)
        return json.dumps(data, ensure_ascii=False)

    # ── 코드 검색 도구 ──

    @lc_tool
    def bundle_search_tool(query: str, kind: str = "symbol") -> str:
        """ChunkBundle에서 심볼/텍스트 검색한다. kind: symbol|text."""
        results = _bundle_search(data_dir, query, kind, span_index=span_index, chunk_index=chunk_index)
        enriched = [_enrich_with_modulename(r) for r in results[:20]]
        logger.info("[TOOL] bundle_search | query=%s kind=%s results=%d", query, kind, len(enriched))
        return json.dumps(enriched, ensure_ascii=False)

    @lc_tool
    def bundle_get_chunk_tool(chunk_id: str) -> str:
        """chunk_id로 코드 조각을 조회한다."""
        r = _bundle_get_chunk(chunk_id, chunk_index=chunk_index)
        if r:
            _enrich_with_modulename(r)
        logger.info("[TOOL] bundle_get_chunk | chunk_id=%s found=%s", chunk_id, bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    # ── 관계 등록 도구 ──

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
        # 자기 참조 방지
        if from_table.upper() == to_table.upper():
            return "error: self-referencing FK not allowed"
        # DDL 테이블 목록 기반 검증
        if ddl_meta:
            known_tables = {n.upper() for (_, n) in ddl_meta.keys()}
            for tbl in (from_table, to_table):
                if tbl.upper() not in known_tables:
                    return f"error: '{tbl}' not found in DDL table list"
        # 중복 검사
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

    # ── DDL 조회 도구 ──

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

    # ─────────────────────────────────────────────────────────────
    # Skill별 도구 조합
    # ─────────────────────────────────────────────────────────────

    return _assemble_tools_for_skill(
        skill_name=skill_name,
        has_ddl=bool(ddl_meta),
        tools_map={
            # 인덱스 조회
            "sql_lookup": sql_lookup_tool,
            "config_lookup": config_lookup_tool,
            "artifact_lookup": artifact_lookup_tool,
            # 코드 검색
            "bundle_search": bundle_search_tool,
            "bundle_get_chunk": bundle_get_chunk_tool,
            # 관계 등록
            "refer_to": graph_create_refer_to_tool,
            "table_access": graph_create_table_access_tool,
            "column": graph_create_column_tool,
            "fk": graph_create_fk_tool,
            # DDL 조회
            "table_search": table_search_tool,
            "table_columns": table_columns_tool,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Skill별 도구 조합 로직
# ═══════════════════════════════════════════════════════════════════════════

# Skill별 필요한 도구 키 목록
_SKILL_TOOL_SETS: Dict[str, List[str]] = {
    "entity-analysis": [
        "bundle_search", "bundle_get_chunk",
        "table_access", "column", "fk",
        # DDL 도구는 has_ddl 시 자동 추가
    ],
    "table-tracking": [
        "bundle_search", "bundle_get_chunk",
        "table_access", "column", "fk",
        "table_search", "table_columns",
    ],
    "sql-tracking": [
        "sql_lookup", "artifact_lookup",
        "bundle_search", "bundle_get_chunk",
        "refer_to", "table_access", "column", "fk",
        # DDL 도구는 has_ddl 시 자동 추가
    ],
    "config-tracking": [
        "config_lookup",
        "bundle_search", "bundle_get_chunk",
        "refer_to",
    ],
}

# DDL 조회 도구 키
_DDL_TOOL_KEYS = ["table_search", "table_columns"]


def _assemble_tools_for_skill(
    skill_name: str,
    has_ddl: bool,
    tools_map: Dict[str, Any],
) -> List[Any]:
    """Skill 이름에 맞는 도구 목록을 조합한다.

    Args:
        skill_name: 활성화된 Skill 이름.
        has_ddl: DDL 메타데이터 존재 여부.
        tools_map: 도구 키 → @tool 함수 매핑.

    Returns:
        해당 Skill에 필요한 도구 함수 리스트.
    """
    # Skill 이름으로 필요 도구 키 목록 조회
    tool_keys = _SKILL_TOOL_SETS.get(skill_name)

    if tool_keys is None:
        # 미등록 Skill 또는 빈 문자열 → 전체 도구 (폴백)
        tool_keys = list(tools_map.keys())

    # DDL 도구 자동 추가 (DDL 있고 + 아직 포함 안 된 경우)
    if has_ddl:
        for key in _DDL_TOOL_KEYS:
            if key not in tool_keys:
                tool_keys.append(key)

    # 키 → 도구 함수 변환 (존재하는 것만)
    return [tools_map[k] for k in tool_keys if k in tools_map]
