"""Neo4j 그래프 도구 — 그래프 조회 + 엣지 등록.

Agent invoke 전에 ``set_graph_context(client, edge_collector)`` 호출 필요.
도구는 ``contextvars`` 로 client / collector 참조 (asyncio task 별 격리).

도구 결과는 라인 1개씩 / fqn 트리만 (속성 X) — $400 사고 차단 정책 유지.
"""
from __future__ import annotations

import contextvars
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain.tools import tool


# ── Agent invoke 별 컨텍스트 ───────────────────────────────

_graph_client_var: contextvars.ContextVar = contextvars.ContextVar(
    "graph_client", default=None
)
_edge_collector_var: contextvars.ContextVar = contextvars.ContextVar(
    "edge_collector", default=None
)


def set_graph_context(client: Any, edge_collector: Optional["ToolResultCollector"] = None) -> None:
    """Agent invoke 직전 호출 — tool 안에서 client / collector 사용 가능."""
    _graph_client_var.set(client)
    _edge_collector_var.set(edge_collector)


def get_edge_collector() -> Optional["ToolResultCollector"]:
    """invoke 결과 수집기 조회 (메인 로직이 invoke 후 snapshot)."""
    return _edge_collector_var.get()


# ── Agent invoke 별 결과 수집기 ────────────────────────────


@dataclass
class ToolResultCollector:
    """Linking Agent invoke 별 그래프 등록 결과 수집 (thread-safe).

    Plan §3.6: invoke 별 quarantine — 각 case 마다 자기 collector 사용.
    contextvars 로 asyncio task 별 격리.
    """
    table_accesses: list[dict] = field(default_factory=list)
    refer_tos: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_table_access(self, function_id: str, table: str, crud: str, evidence: str) -> None:
        with self._lock:
            self.table_accesses.append({
                "function_id": function_id,
                "table": table,
                "crud": crud,
                "evidence": evidence,
            })

    def add_refer_to(
        self,
        function_id: str,
        target_type: str,
        target_key: str,
        evidence: str,
    ) -> None:
        with self._lock:
            self.refer_tos.append({
                "function_id": function_id,
                "target_type": target_type,
                "target_key": target_key,
                "evidence": evidence,
            })

    def snapshot_and_clear(self) -> dict:
        with self._lock:
            snapshot = {
                "table_accesses": list(self.table_accesses),
                "refer_tos": list(self.refer_tos),
            }
            self.table_accesses.clear()
            self.refer_tos.clear()
            return snapshot


# ── 도구 ──────────────────────────────────────────────────


@tool
async def ddl_lookup(table_name: str) -> str:
    """DDL 테이블 + 컬럼 + PK + FK 조회 (Neo4j Table/Column)."""
    client = _graph_client_var.get()
    if client is None:
        return f"GRAPH_CONTEXT_NOT_SET: {table_name}"

    query = """
    MATCH (t:Table)
    WHERE toUpper(t.name) = toUpper($name)
    OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
    WITH t, collect(DISTINCT {name: c.name, dtype: c.dtype, pk: c.is_primary_key, nullable: c.nullable}) AS columns
    OPTIONAL MATCH (t)-[fk:FK]->(target:Table)
    WITH t, columns, collect(DISTINCT {from_col: fk.from_column, to_table: target.name, to_col: fk.to_column}) AS fks
    RETURN t.db AS db, t.schema AS schema, t.name AS name, t.description AS description, columns, fks
    LIMIT 1
    """
    try:
        rows = await _run_read(client, query, {"name": table_name})
        if not rows:
            return f"NOT_FOUND: {table_name}"
        return json.dumps(rows[0], ensure_ascii=False, indent=2)
    except Exception as e:
        return f"QUERY_ERROR: {e}"


@tool
async def graph_get_callers(function_id: str, depth: int = 2) -> str:
    """N홉 caller fqn 트리 (속성 X — fqn 만)."""
    if depth > 3:
        depth = 3
    client = _graph_client_var.get()
    if client is None:
        return "GRAPH_CONTEXT_NOT_SET"

    query = f"""
    MATCH path = (caller:Function)-[:CALLS*1..{depth}]->(f:Function {{function_id: $fid}})
    RETURN [n in nodes(path) | n.function_id] AS fqn_path
    LIMIT 50
    """
    try:
        rows = await _run_read(client, query, {"fid": function_id})
        return "\n".join(" → ".join(r["fqn_path"]) for r in rows) or "NO_CALLERS"
    except Exception as e:
        return f"QUERY_ERROR: {e}"


@tool
async def graph_get_callees(function_id: str, depth: int = 2) -> str:
    """N홉 callee fqn 트리."""
    if depth > 3:
        depth = 3
    client = _graph_client_var.get()
    if client is None:
        return "GRAPH_CONTEXT_NOT_SET"

    query = f"""
    MATCH path = (f:Function {{function_id: $fid}})-[:CALLS*1..{depth}]->(callee:Function)
    RETURN [n in nodes(path) | n.function_id] AS fqn_path
    LIMIT 50
    """
    try:
        rows = await _run_read(client, query, {"fid": function_id})
        return "\n".join(" → ".join(r["fqn_path"]) for r in rows) or "NO_CALLEES"
    except Exception as e:
        return f"QUERY_ERROR: {e}"


@tool
def graph_create_table_access(function_id: str, table: str, crud: str, evidence: str) -> str:
    """READS/WRITES 엣지 등록 (collector 에 누적 — invoke 종료 후 메인이 flush)."""
    collector = _edge_collector_var.get()
    if collector is None:
        return "EDGE_COLLECTOR_NOT_SET"
    crud_upper = crud.upper()
    if crud_upper not in ("READ", "WRITE", "READS", "WRITES"):
        return f"INVALID_CRUD: {crud}"
    crud_norm = "READS" if "READ" in crud_upper else "WRITES"
    collector.add_table_access(function_id, table, crud_norm, evidence)
    return f"OK: {function_id} {crud_norm} {table}"


@tool
def graph_create_refer_to(
    function_id: str,
    target_type: str,
    target_key: str,
    evidence: str,
) -> str:
    """REFER_TO 엣지 등록 — Function → SqlStatement / ConfigSection / UnresolvedRef.

    Args:
        function_id: caller Function 의 fqn.
        target_type: ``"sql_statement"`` / ``"config_section"`` / ``"unresolved"``.
        target_key: 대상 식별자 (sql_key / config_key / unresolved key 등).
        evidence: ``"file:line"`` 형식 출처.
    """
    collector = _edge_collector_var.get()
    if collector is None:
        return "EDGE_COLLECTOR_NOT_SET"
    if target_type not in ("sql_statement", "config_section", "unresolved"):
        return f"INVALID_TARGET_TYPE: {target_type}"
    collector.add_refer_to(function_id, target_type, target_key, evidence)
    return f"OK: REFER_TO {function_id} → {target_type}/{target_key}"


# ── 내부 헬퍼 ─────────────────────────────────────────────


async def _run_read(client, query: str, params: dict) -> list[dict]:
    """``Neo4jClient.run_read_query`` 헬퍼 (없으면 driver 직접)."""
    if hasattr(client, "run_read_query"):
        return await client.run_read_query(query, params)
    # fallback: driver 직접 (Neo4jClient 의 private 속성)
    async with client._driver.session(database=client._database) as session:
        result = await session.run(query, **params)
        return await result.data()
