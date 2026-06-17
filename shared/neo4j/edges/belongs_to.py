"""BELONGS_TO 엣지 — Table → Schema."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import BelongsTo
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import BELONGS_TO


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (t:TABLE {{id: item.child_id}})
MATCH (s:SCHEMA {{id: item.parent_id}})
MERGE (t)-[:{BELONGS_TO}]->(s)
"""


async def upsert_belongs_to(client: Neo4jClient, edges: Iterable[BelongsTo]) -> None:
    items = [{"child_id": e.child_id, "parent_id": e.parent_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
