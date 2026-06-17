"""AFFECTS_TABLE 엣지 — Example → Table.

Example 의 ``affected_tables[]`` 에서 도출 — 단일 영역에 op (READ/INSERT/UPDATE/
DELETE/MERGE) 가 들어있음. Table 은 이름(대소문자 무시) 매칭 — DDL Table 노드.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import AffectsTable
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import AFFECTS_TABLE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (e:EXAMPLE {{id: item.source_id}})
MATCH (t:TABLE) WHERE toLower(t.name) = toLower(item.table_name)
MERGE (e)-[:{AFFECTS_TABLE} {{op: item.op}}]->(t)
"""


async def upsert_affects_table(client: Neo4jClient, edges: Iterable[AffectsTable]) -> None:
    """Example → Table 엣지 (op 별)."""
    items = [
        {"source_id": e.source_id, "table_name": e.table_name, "op": e.op}
        for e in edges
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
