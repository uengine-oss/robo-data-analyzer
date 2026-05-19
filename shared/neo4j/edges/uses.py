"""USES 엣지 — Module → Module."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import Uses
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MATCH (from:Module {module_id: item.from_module_id})
MATCH (to:Module {module_id: item.to_module_id})
MERGE (from)-[r:USES]->(to)
SET r.scope = item.scope
"""


async def upsert_uses(client: Neo4jClient, edges: Iterable[Uses]) -> None:
    items = [{
        "from_module_id": e.from_module_id,
        "to_module_id": e.to_module_id,
        "scope": e.scope,
    } for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
