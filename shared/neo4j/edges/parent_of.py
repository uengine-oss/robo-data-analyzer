"""PARENT_OF 엣지 — Function → nested Function (DBMS 의 nested range)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import ParentOf
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MATCH (parent:Function {function_id: item.parent_function_id})
MATCH (child:Function {function_id: item.child_function_id})
MERGE (parent)-[:PARENT_OF]->(child)
"""


async def upsert_parent_of(client: Neo4jClient, edges: Iterable[ParentOf]) -> None:
    items = [{
        "parent_function_id": e.parent_function_id,
        "child_function_id": e.child_function_id,
    } for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
