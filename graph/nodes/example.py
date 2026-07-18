"""EXAMPLE 노드 Cypher — Rule 의 given/when/then 시나리오 (Example Mapping).

``when`` / ``then`` 은 Cypher 예약어 — Neo4j 속성명은 ``when_`` / ``then_`` 로 회피
(dataclass 필드는 ``when`` / ``then``, upsert 가 매핑).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Example
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER

_UPSERT_QUERY = f"""
UNWIND $items AS item
MERGE (e:EXAMPLE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
SET e.given = item.given,
    e.when_ = item.when_,
    e.then_ = item.then_,
    e.graph_owner = '{ANALYZER_GRAPH_OWNER}'
"""


async def upsert_examples(client: Neo4jClient, examples: Iterable[Example]) -> None:
    """EXAMPLE 노드 MERGE."""
    items = [
        {
            "id": e.id,
            "given": e.given,
            "when_": e.when,
            "then_": e.then,
        }
        for e in examples
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
