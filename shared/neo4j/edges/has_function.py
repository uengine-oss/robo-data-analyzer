"""HAS_FUNCTION 엣지 — Module → Function (모듈이 함수를 품음)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasFunction
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_FUNCTION


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (m:MODULE {{module_id: item.module_id}})
MATCH (f:FUNCTION {{function_id: item.function_id}})
MERGE (m)-[:{HAS_FUNCTION}]->(f)
"""


async def upsert_has_function(client: Neo4jClient, edges: Iterable[HasFunction]) -> None:
    items = [{"module_id": e.module_id, "function_id": e.function_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
