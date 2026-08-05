"""AFFECTS_TABLE 엣지 — Rule → Table (규칙 단위 결정론 귀속, spec 116).

``rule_table_effects`` 가 then 효과를 실제 R/W Table ID 로 확정한 뒤 이 계층에
도착한다. 저장기는 그 ID 를 다시 이름으로 축약하지 않고 Analyzer 소유 Table 하나에만
연결한다.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import AffectsTable
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import AFFECTS_TABLE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (r:RULE {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (t:TABLE {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (r)-[a:{AFFECTS_TABLE} {{op: item.op}}]->(t)
SET a.access = item.access,
    a.op_source = item.op_source
"""


async def upsert_affects_table(client: Neo4jClient, edges: Iterable[AffectsTable]) -> None:
    """Rule → Table 엣지 (op 별)."""
    items = [
        {
            "source_id": e.source_id,
            "target_id": e.target_id,
            "access": e.access,
            "op": e.op,
            "op_source": e.op_source,
        }
        for e in edges
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
