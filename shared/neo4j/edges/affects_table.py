"""AFFECTS_TABLE 엣지 — Example → Table.

Example 의 ``affected_tables[]`` 에서 도출 — 단일 영역에 op (READ/INSERT/UPDATE/
DELETE/MERGE) 가 들어있음. Table 은 **마지막 세그먼트**(스키마 접두사·대소문자 무시)
로 매칭 — dbms 는 ``스키마.테이블`` 로 오나 TABLE 노드는 bare 라 접두사를 흡수한다.

``item.table_name`` 은 두 형태로 온다(spec 056 US2): constrain_tables_to_rw 가 TABLE
**id**(``db:order_chk_hst``, **콜론** 구분)로 치환한 것, 또는 LLM 이 낸 bare 이름(``orders``).
콜론 접두사(``db:``)를 먼저 벗기고 그다음 점 접두사를 벗겨야 TABLE(``name=order_chk_hst``)과
매칭된다 — 점만 분리하면 ``db:`` 가 남아 매칭 실패(엣지 0)한다.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import AffectsTable
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import AFFECTS_TABLE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (e:EXAMPLE {{id: item.source_id}})
MATCH (t:TABLE)
WHERE toLower(last(split(t.name, '.')))
    = toLower(last(split(last(split(item.table_name, ':')), '.')))
MERGE (e)-[:{AFFECTS_TABLE} {{op: item.op}}]->(t)
"""


async def upsert_affects_table(client: Neo4jClient, edges: Iterable[AffectsTable]) -> None:
    """Example → Table 엣지 (op 별)."""
    items = [
        {"source_id": e.source_id, "table_name": e.table_name, "op": e.op}
        for e in edges
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
