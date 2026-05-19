"""CALLS 엣지 — Function → Function (호출 관계)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import Calls
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MATCH (caller:Function {function_id: item.caller_function_id})
MATCH (callee:Function {function_id: item.callee_function_id})
MERGE (caller)-[r:CALLS]->(callee)
SET r.scope = item.scope
"""


async def upsert_calls(client: Neo4jClient, edges: Iterable[Calls]) -> None:
    items = [{
        "caller_function_id": e.caller_function_id,
        "callee_function_id": e.callee_function_id,
        "scope": e.scope,
    } for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
