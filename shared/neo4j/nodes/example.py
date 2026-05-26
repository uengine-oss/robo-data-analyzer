"""EXAMPLE 노드 Cypher — Rule 의 given/when/then 시나리오 (Example Mapping).

``when`` / ``then`` 은 Cypher 예약어 — Neo4j 속성명은 ``when_`` / ``then_`` 로 회피
(dataclass 필드는 ``when`` / ``then``, upsert 가 매핑).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Example
from shared.neo4j.neo4j_client import Neo4jClient

_UPSERT_QUERY = """
UNWIND $items AS item
MERGE (e:EXAMPLE {example_id: item.example_id})
SET e.description = item.description,
    e.given = item.given,
    e.when_ = item.when_,
    e.then_ = item.then_,
    e.is_boundary = item.is_boundary
"""


async def upsert_examples(client: Neo4jClient, examples: Iterable[Example]) -> None:
    """EXAMPLE 노드 MERGE."""
    items = [
        {
            "example_id": e.example_id,
            "description": e.description,
            "given": e.given,
            "when_": e.when,
            "then_": e.then,
            "is_boundary": e.is_boundary,
        }
        for e in examples
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
