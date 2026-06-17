"""PARENT_OF 엣지 — Function → nested Function (DBMS 의 nested range)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import ParentOf
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import PARENT_OF


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (parent {{id: item.parent_id}})
MATCH (child {{id: item.child_id}})
MERGE (parent)-[:{PARENT_OF}]->(child)
"""


async def upsert_parent_of(client: Neo4jClient, edges: Iterable[ParentOf]) -> None:
    items = [{
        "parent_id": e.parent_id,
        "child_id": e.child_id,
    } for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
