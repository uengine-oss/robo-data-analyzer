"""HAS_QUESTION 엣지 — CodeNode → Question (Example Mapping)."""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import HasQuestion
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import HAS_QUESTION

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
MATCH (q:QUESTION {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
MERGE (node)-[rel:{HAS_QUESTION}]->(q)
SET rel.generation_id = $generation_id
"""


async def upsert_has_question(client: Neo4jClient, edges: Iterable[HasQuestion]) -> None:
    """CodeNode → Question 엣지."""
    items = [{"source_id": e.source_id, "target_id": e.target_id} for e in edges]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
