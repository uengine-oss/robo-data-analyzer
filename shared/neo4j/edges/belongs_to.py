"""BELONGS_TO 엣지 — Table → Schema."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import BelongsTo
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MATCH (t:Table {fqn: item.table_fqn})
MATCH (s:Schema {fqn: item.schema_fqn})
MERGE (t)-[:BELONGS_TO]->(s)
"""


async def upsert_belongs_to(client: Neo4jClient, edges: Iterable[BelongsTo]) -> None:
    items = [{"table_fqn": e.table_fqn, "schema_fqn": e.schema_fqn} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
