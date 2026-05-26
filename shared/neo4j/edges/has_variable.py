"""HAS_VARIABLE 엣지 — Module → Variable (모듈 레벨 변수)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasVariable
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_VARIABLE


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (m:MODULE {{module_id: item.module_id}})
MATCH (v:VARIABLE {{variable_id: item.variable_id}})
MERGE (m)-[:{HAS_VARIABLE}]->(v)
"""


async def upsert_has_variable(client: Neo4jClient, edges: Iterable[HasVariable]) -> None:
    items = [{"module_id": e.module_id, "variable_id": e.variable_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
