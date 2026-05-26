"""HAS_QUESTION 엣지 — Function → Question (Example Mapping)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasQuestion
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_QUESTION

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (f:FUNCTION {{function_id: item.function_id}})
MATCH (q:QUESTION {{question_id: item.question_id}})
MERGE (f)-[:{HAS_QUESTION}]->(q)
"""


async def upsert_has_question(client: Neo4jClient, edges: Iterable[HasQuestion]) -> None:
    """Function → Question 엣지."""
    items = [{"function_id": e.function_id, "question_id": e.question_id} for e in edges]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
