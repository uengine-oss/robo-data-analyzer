"""HAS_EXAMPLE 엣지 — Rule → Example (Example Mapping)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasExample
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_EXAMPLE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (r:RULE {{rule_hash: item.rule_hash}})
MATCH (e:EXAMPLE {{example_id: item.example_id}})
MERGE (r)-[:{HAS_EXAMPLE}]->(e)
"""


async def upsert_has_example(client: Neo4jClient, edges: Iterable[HasExample]) -> None:
    """Rule → Example 엣지."""
    items = [{"rule_hash": e.rule_hash, "example_id": e.example_id} for e in edges]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
