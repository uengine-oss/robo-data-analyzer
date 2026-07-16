"""QUESTION 노드 Cypher — 코드만으로 답 안 되는 모호점 (Example Mapping · 사람 검토 큐)."""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Question
from graph.neo4j_client import Neo4jClient

_UPSERT_QUERY = """
UNWIND $items AS item
MERGE (q:QUESTION {id: item.id})
SET q.text = item.text,
    q.reason = item.reason
"""


async def upsert_questions(client: Neo4jClient, questions: Iterable[Question]) -> None:
    """QUESTION 노드 MERGE."""
    items = [
        {"id": q.id, "text": q.text, "reason": q.reason}
        for q in questions
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
