from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

from config.settings import settings
from analyzer.strategy.framework.common.models import (
    FunctionTableEdge,
    ReferToEdge,
    ArtifactChunk,
    SqlStatementChunk,
    ConfigSectionChunk,
)
from analyzer.strategy.framework.common.models import FkEdge

logger = logging.getLogger(__name__)


def sql_lookup(sql_key: str, sql_index: Dict[str, SqlStatementChunk]) -> Optional[Dict[str, Any]]:
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
    """hint_key 또는 chunk_id 로 ArtifactChunk 를 찾기 위한 역인덱스."""
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


def build_tools(
    state: Dict[str, Any],
    function_id: str,
    refer_collector: List[ReferToEdge],
    table_collector: List[FunctionTableEdge],
    fk_collector: Optional[List] = None,
    variable_id: Optional[str] = None,
    tool_scope: str = "full",
    collector_lock: Optional[threading.Lock] = None,
    variable_ref_collector: Optional[List] = None,
) -> List[Any]:
    """LangChain/deepagents 호환 툴 목록 생성. ChunkBundle + 인덱스 + 그래프 조작만 노출.

    tool_scope:
      - "full": 모든 도구 (미매칭 키 추적 등)
      - "entity": bundle 검색 + 테이블 접근 + FK (엔티티 식별 전용)
    """
    from langchain_core.tools import tool
    from analyzer.strategy.framework.common.utils import sql_op_to_method_relation as _sql_op_to_method_relation

    if fk_collector is None:
        fk_collector = []

    lock = collector_lock if collector_lock is not None else threading.Lock()
    default_confidence = settings.deep_agent.default_confidence
    if tool_scope == "unmatched":
        tool_scope = "full"

    # ── 기존 FK 세트 (DDL 파싱 — 중복 생성 방지) ───────────
    ddl_meta = state.get("ddl_table_metadata") or {}
    existing_fk_set: set = set()
    for (_s, _n), info in ddl_meta.items():
        for fk in (info.get("foreign_keys") or info.get("foreignKeys") or []):
            fk_col = (fk.get("column") or "").strip().upper()
            fk_ref = (fk.get("ref") or "").strip().upper()
            if fk_col and fk_ref:
                existing_fk_set.add((_n.upper(), fk_col, fk_ref))

    # ── state 인덱스 참조 ──────────────────────────────────
    sql_index: Dict[str, SqlStatementChunk] = state.get("sql_index") or {}
    config_index: Dict[str, ConfigSectionChunk] = state.get("config_index") or {}
    artifact_index: Dict[str, ArtifactChunk] = state.get("artifact_index") or {}
    artifact_key_index = build_artifact_lookup_index(artifact_index)
    span_index = state.get("span_index") or {}
    chunk_index = state.get("chunk_index") or {}
    data_dir = state.get("data_dir") or ""

    from analyzer.strategy.framework.parsing.chunk_bundle import (
        bundle_search as _bundle_search,
        bundle_get_chunk as _bundle_get_chunk,
    )

    def _enrich_with_modulename(result: Dict[str, Any]) -> Dict[str, Any]:
        """filePath에서 moduleName 추출하여 결과에 추가."""
        fp = result.get("filePath", "")
        if fp:
            fname = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            name = fname.rsplit(".", 1)[0] if "." in fname else fname
            result["moduleName"] = name
        return result

    # ===== 인덱스 조회 도구 ==========================================

    @tool
    def sql_lookup_tool(sql_key: str) -> str:
        """sql_key로 SQL을 조회한다."""
        logger.info("[FW:DEEPAGENT:TOOL] sql_lookup | key=%s", sql_key)
        r = sql_lookup(sql_key, sql_index)
        logger.info("[FW:DEEPAGENT:TOOL] sql_lookup | found=%s", bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def config_lookup_tool(config_key: str) -> str:
        """config_key로 설정 엔트리를 조회한다."""
        logger.info("[FW:DEEPAGENT:TOOL] config_lookup | key=%s", config_key)
        r = config_lookup(config_key, config_index)
        logger.info("[FW:DEEPAGENT:TOOL] config_lookup | found=%s", bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    @tool
    def artifact_lookup_tool(key: str) -> str:
        """chunk_id 또는 hint_key(ejb-name)로 ArtifactChunk를 조회한다."""
        logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | key=%s", key)
        cid = artifact_key_index.get((key or "").strip())
        if not cid:
            logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | found=False")
            return ""
        ac = artifact_index.get(cid)
        if not ac:
            logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | found=False")
            return ""
        data = {
            "chunk_id": cid,
            "chunk_type": getattr(ac, "chunk_type", ""),
            "hint_key": getattr(ac, "hint_key", ""),
            "file_path": getattr(ac, "source_path", ""),
            "start_line": getattr(ac, "start_line", 0),
            "end_line": getattr(ac, "end_line", 0),
            "text": getattr(ac, "text", "")[:2000],
        }
        logger.info("[FW:DEEPAGENT:TOOL] artifact_lookup | found=True chunk_id=%s", cid)
        return json.dumps(data, ensure_ascii=False)

    # ===== 코드 검색 도구 ==========================================

    @tool
    def bundle_search_tool(query: str, kind: str = "symbol") -> str:
        """ChunkBundle에서 심볼/텍스트 검색. kind: symbol|text."""
        logger.info("[FW:DEEPAGENT:TOOL] bundle_search | query=%s kind=%s", query, kind)
        results = _bundle_search(data_dir, query, kind, span_index=span_index, chunk_index=chunk_index)
        enriched = [_enrich_with_modulename(r) for r in results[:20]]
        logger.info("[FW:DEEPAGENT:TOOL] bundle_search | results=%d", len(enriched))
        return json.dumps(enriched, ensure_ascii=False)

    @tool
    def bundle_get_chunk_tool(chunk_id: str) -> str:
        """chunk_id로 코드 조각 조회."""
        logger.info("[FW:DEEPAGENT:TOOL] bundle_get_chunk | chunk_id=%s", chunk_id)
        r = _bundle_get_chunk(chunk_id, chunk_index=chunk_index)
        if r:
            _enrich_with_modulename(r)
        logger.info("[FW:DEEPAGENT:TOOL] bundle_get_chunk | found=%s", bool(r))
        return json.dumps(r, ensure_ascii=False) if r else ""

    # ===== 관계 등록 도구 ==========================================

    @tool
    def graph_create_refer_to_tool(target_type: str, target_key: str, evidence: str = "", source_function_id: str = "") -> str:
        """REFER_TO 엣지 생성. source_function_id 비워두면 기본 function_id 사용."""
        mid = source_function_id.strip() if source_function_id else function_id
        logger.info("[FW:DEEPAGENT:TOOL] create_refer_to | target_type=%s target_key=%s source=%s", target_type, target_key, mid)
        if not mid:
            return "error: source_function_id required (no default function_id)"
        with lock:
            refer_collector.append(
                ReferToEdge(
                    function_id=mid,
                    target_type=target_type,
                    target_id=target_key,
                    evidence={"note": evidence} if evidence else {},
                    confidence=default_confidence,
                    variable_id=variable_id,
                )
            )
        return "ok"

    @tool
    def graph_create_table_access_tool(table_name: str, access: str, evidence: str = "", source_function_id: str = "") -> str:
        """READS/WRITES 엣지 생성. source_function_id 비워두면 기본 function_id 사용."""
        mid = source_function_id.strip() if source_function_id else function_id
        rel = access.upper() if access else "READS"
        if rel not in ("READS", "WRITES"):
            rel = "READS"
        logger.info("[FW:DEEPAGENT:TOOL] create_table_access | table=%s access=%s source=%s", table_name, rel, mid)
        if not mid:
            return "error: source_function_id required (no default function_id)"
        with lock:
            table_collector.append(
                FunctionTableEdge(
                    function_id=mid,
                    table_schema="",
                    table_name=table_name,
                    relation=_sql_op_to_method_relation("INSERT" if rel == "WRITES" else "SELECT"),
                    variable_id=variable_id,
                )
            )
        return "ok"

    # ===== FK 관련 도구 ============================================

    @tool
    def table_search_tool(keyword: str) -> str:
        """테이블명을 키워드로 검색. 대소문자/언더스코어 무시."""
        logger.info("[FW:DEEPAGENT:TOOL] table_search | keyword=%s", keyword)
        keyword_lower = keyword.lower().replace("_", "")
        results = []
        for (schema, name) in ddl_meta:
            name_lower = name.lower().replace("_", "")
            if keyword_lower in name_lower:
                results.append({"schema": schema, "name": name})
        logger.info("[FW:DEEPAGENT:TOOL] table_search | results=%d", len(results))
        if not results:
            return "no matching tables found"
        return json.dumps(results[:20], ensure_ascii=False)

    @tool
    def table_columns_tool(table_name: str, table_schema: str = "") -> str:
        """테이블의 컬럼 목록과 PK 정보 조회."""
        logger.info("[FW:DEEPAGENT:TOOL] table_columns | table=%s schema=%s", table_name, table_schema)
        for (s, n), info in ddl_meta.items():
            if n.lower() == table_name.lower() and (not table_schema or s.lower() == table_schema.lower()):
                columns = info.get("columns") or []
                pks = info.get("primary_keys") or []
                col_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in columns]
                logger.info("[FW:DEEPAGENT:TOOL] table_columns | found=True columns=%d", len(col_names))
                return json.dumps({"schema": s, "name": n, "primary_keys": pks, "columns": col_names}, ensure_ascii=False)
        logger.info("[FW:DEEPAGENT:TOOL] table_columns | found=False")
        return f"table '{table_name}' not found"

    @tool
    def graph_create_fk_tool(from_table: str, from_column: str, to_table: str, to_column: str, evidence: str = "", from_schema: str = "", to_schema: str = "") -> str:
        """FK 관계 등록. CREATE TABLE의 REFERENCES 절, SQL JOIN 패턴, 또는 필드명 관례(xxx_id)에서 FK를 발견하면 호출. from_table은 FK 컬럼을 가진 쪽, to_table은 참조되는 PK 쪽."""
        logger.info("[FW:DEEPAGENT:TOOL] create_fk | %s.%s → %s.%s", from_table, from_column, to_table, to_column)
        if from_table.upper() == to_table.upper():
            logger.info("[FW:DEEPAGENT:TOOL] create_fk | result=self_reference_skipped")
            return "error: self-referencing FK not allowed"
        _NON_ENTITY_SUFFIXES = ("Service", "Manager", "Controller", "Repository", "Handler", "Dao", "Helper", "Utils", "Factory")
        for tbl in (from_table, to_table):
            if any(tbl.endswith(s) for s in _NON_ENTITY_SUFFIXES):
                logger.info("[FW:DEEPAGENT:TOOL] create_fk | result=non_entity_rejected (%s)", tbl)
                return f"error: '{tbl}' is a service/utility class, not an entity table. FK must connect entity tables only."
        ref_key = f"{to_table}.{to_column}".upper()
        check_key = (from_table.upper(), from_column.upper(), ref_key)
        if check_key in existing_fk_set:
            logger.info("[FW:DEEPAGENT:TOOL] create_fk | result=duplicate_skipped")
            return "already exists (from DDL)"
        existing_fk_set.add(check_key)
        with lock:
            fk_collector.append(FkEdge(
                from_schema=from_schema, from_table=from_table, from_column=from_column,
                to_schema=to_schema, to_table=to_table, to_column=to_column,
                candidate=True,
            ))
        logger.info("[FW:DEEPAGENT:TOOL] create_fk | result=ok")
        return "ok"

    # ── 변수 참조 등록 도구 ──
    @tool
    def graph_create_variable_ref_tool(function_id: str, variable_id: str, evidence: str = "") -> str:
        """함수→변수 REFERENCES 관계를 등록합니다."""
        if not function_id or not variable_id:
            return "error: function_id and variable_id required"
        logger.info("[FW:DEEPAGENT:TOOL] create_variable_ref | function=%s variable=%s", function_id, variable_id)
        with lock:
            if variable_ref_collector is not None:
                variable_ref_collector.append((function_id, variable_id))
        return "ok"

    if tool_scope == "variable_reference":
        return [
            bundle_search_tool,
            graph_create_variable_ref_tool,
        ]

    if tool_scope == "entity":
        tools = [
            bundle_search_tool, bundle_get_chunk_tool,
            graph_create_table_access_tool, graph_create_fk_tool,
        ]
        if ddl_meta:
            tools.extend([table_search_tool, table_columns_tool])
        return tools

    tools = [
        sql_lookup_tool, config_lookup_tool, artifact_lookup_tool,
        bundle_search_tool, bundle_get_chunk_tool,
        graph_create_refer_to_tool, graph_create_table_access_tool,
        graph_create_fk_tool,
    ]
    if ddl_meta:
        tools.extend([table_search_tool, table_columns_tool])
    return tools
