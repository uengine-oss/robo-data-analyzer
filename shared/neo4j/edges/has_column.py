"""HAS_COLUMN 엣지 — Table → Column."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasColumn
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_COLUMN


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (t:TABLE {{fqn: item.table_fqn}})
MATCH (c:COLUMN {{fqn: item.column_fqn}})
MERGE (t)-[:{HAS_COLUMN}]->(c)
"""


async def upsert_has_column(client: Neo4jClient, edges: Iterable[HasColumn]) -> None:
    items = [{"table_fqn": e.table_fqn, "column_fqn": e.column_fqn} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
