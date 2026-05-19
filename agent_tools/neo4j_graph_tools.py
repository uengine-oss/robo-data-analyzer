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
    """DDL 테이블 정확 매칭 — 컬럼 / PK / FK 조회.

    **언제 사용** (Linking Agent):
    - bundle_search 로 entity 후보 발견 후 DDL 실제 존재 확인.
    - ddl_transform 변환 (camel_to_snake_upper 등) 적용 후 매칭 확인.
    - FK 단서 확인 (이 테이블이 다른 테이블 어디로 FK 거는지).

    **인자**:
    - `table_name`: 테이블명 (대소문자 무시 — `toUpper` 비교).

    **결과 형식** (JSON):
    ```json
    {
      "db": "myapp", "schema": "PUBLIC", "name": "ORDER",
      "description": "...",
      "columns": [{"name": "ID", "dtype": "BIGINT", "pk": true, "nullable": false}, ...],
      "fks": [{"from_col": "USER_ID", "to_table": "USERS", "to_col": "ID"}, ...]
    }
    ```

    **실패 응답**:
    - `NOT_FOUND: <name>` — DDL 에 없음. ddl_transform 의 다른 변형 / fallback_prefixes 시도.
    - `GRAPH_CONTEXT_NOT_SET` — invoke 직전 `set_graph_context` 누락 (운영 버그).
    - `QUERY_ERROR: ...` — Cypher 실행 오류.
    """
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
    """N홉 caller **fqn 트리만** (노드 속성 X — 토큰 절감).

    **언제 사용** (Linking Agent):
    - wrapper / virtual_dispatch case 추적 — "이 함수를 누가 호출하는가?".
    - REFER_TO 엣지 등록 시 어디 caller 에 붙일지 결정.
    - Bottom-up 분석 의존성 확인.

    **인자**:
    - `function_id`: callee 함수 fqn.
    - `depth`: N홉 (default 2, 최대 3). 3 초과 입력은 자동 3 으로 trim.

    **결과 형식**:
    - 한 줄당 fqn 경로 (`A → B → C`). 최대 50 라인.
    - 노드 속성 (signature / file / line) X — 필요하면 별도 도구.

    **실패 응답**:
    - `NO_CALLERS` — caller 0개 (root / entry point).
    - `GRAPH_CONTEXT_NOT_SET` — invoke 직전 컨텍스트 누락.
    - `QUERY_ERROR: ...` — Cypher 오류.
    """
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
    """N홉 callee **fqn 트리만** (속성 X).

    **언제 사용** (Linking Agent):
    - wrapper_unwrap case — "이 함수가 안에서 어떤 함수를 호출하는가?"
      안쪽 callee 본문이 SQL / DAO 호출 숨김.
    - virtual_dispatch case — 구현 후보 식별 (depth=1 로 직접 호출만).
    - Bottom-up 분석에서 callee summary 의존성 확인.

    **인자**:
    - `function_id`: caller 함수 fqn.
    - `depth`: N홉 (default 2, 최대 3). 3 초과 자동 trim.

    **결과 형식**:
    - 한 줄당 fqn 경로 (`A → B → C`). 최대 50 라인.

    **실패 응답**:
    - `NO_CALLEES` — callee 0개 (leaf function).
    - `GRAPH_CONTEXT_NOT_SET` — invoke 컨텍스트 누락.
    - `QUERY_ERROR: ...` — Cypher 오류.

    **운영 팁**:
    - depth=1 부터 시작. 3 까지 가도 fqn 만이라 토큰 폭증 X.
    - callee fqn 보고 본문 필요하면 `bundle_search_lines("<callee_name>")` 로 위치 확인 후 `read_file_range`.
    """
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
    """**Function → Table READS/WRITES 엣지 등록**.

    **언제 사용** (Linking Agent):
    - 본문에서 "DB 접근 패턴 + 대상 식별자" **직접 인용 가능** 할 때만.
    - 추측 / 함수 이름 prefix / 컬럼명 일치 / 도메인 추측 등록 X — 추측이면 `resolved=False` + `diagnosis` 가 정답.
    - 한 본문에서 여러 테이블 / 여러 op 보이면 **모두** 등록 (첫 매칭 후 멈추기 X).

    **인자**:
    - `function_id`: caller Function 의 fqn.
    - `table`: 테이블 fqn (`db.schema.name` 또는 `name`). `ddl_lookup` 으로 DDL 매칭 확인 후 그 이름.
    - `crud`: `READ` / `READS` / `WRITE` / `WRITES`. 자동 정규화 (READ → READS / WRITE → WRITES).
    - `evidence`: `file:line` 형식. 인용 가능한 정확한 위치. 인용 불가면 등록 X.

    **결과 응답**:
    - `OK: <function_id> <crud> <table>` — collector 에 누적 (invoke 종료 후 메인이 flush).
    - `INVALID_CRUD: <crud>` — 허용 값 외.
    - `EDGE_COLLECTOR_NOT_SET` — invoke 컨텍스트 누락.

    **금지**:
    - DDL 매칭 안 된 테이블명 등록 (`ddl_lookup` 결과 `NOT_FOUND` 후 등록 X — 추측 환각).
    - 같은 함수에 같은 테이블·op 중복 등록.
    """
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
    """**Function → SqlStatement / ConfigSection / UnresolvedRef REFER_TO 엣지 등록**.

    **언제 사용** (Linking Agent):
    - SQL 리터럴 / mapper namespace.id 참조 — `target_type="sql_statement"`.
    - 설정 키 참조 (`application.yml` 의 `spring.datasource.*` 등) — `target_type="config_section"`.
    - 추적 불가 (variable_alias / dynamic_sql / virtual_dispatch) — `target_type="unresolved"` 로 사람 검토 큐에 박음.

    **인자**:
    - `function_id`: caller Function 의 fqn.
    - `target_type`: `sql_statement` / `config_section` / `unresolved` 중 1개.
    - `target_key`: 대상 식별자 (mapper sql_key / config 키 / unresolved 단서 텍스트).
    - `evidence`: `file:line` 형식 — 본문에서 인용 가능한 정확한 위치.

    **결과 응답**:
    - `OK: REFER_TO <function_id> → <target_type>/<target_key>` — collector 누적.
    - `INVALID_TARGET_TYPE: <type>` — 허용 값 외.
    - `EDGE_COLLECTOR_NOT_SET` — invoke 컨텍스트 누락.

    **운영 팁**:
    - 결정론 매칭 실패 + 추측 등록 대신 `unresolved` REFER_TO 로 박으면 사람 검토 큐 행 → 환각 회피.
    - 같은 함수에 여러 SQL 참조 있으면 모두 등록 (sql_key 별 1 행).
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
