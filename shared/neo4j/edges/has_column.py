"""HAS_COLUMN 엣지 — Table → Column."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasColumn
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_COLUMN


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (t:TABLE {{id: item.parent_id}})
MATCH (c:COLUMN {{id: item.child_id}})
MERGE (t)-[:{HAS_COLUMN}]->(c)
"""


async def upsert_has_column(client: Neo4jClient, edges: Iterable[HasColumn]) -> None:
    items = [{"parent_id": e.parent_id, "child_id": e.child_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
