"""HAS_EXAMPLE 엣지 — Rule → Example (Example Mapping)."""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import HasExample
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import HAS_EXAMPLE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (r:RULE {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (e:EXAMPLE {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (r)-[:{HAS_EXAMPLE}]->(e)
"""


async def upsert_has_example(client: Neo4jClient, edges: Iterable[HasExample]) -> None:
    """Rule → Example 엣지."""
    items = [{"source_id": e.source_id, "target_id": e.target_id} for e in edges]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
