"""EXAMPLE 노드 Cypher — Rule 검증 시나리오 (파이썬 leaf, spec 116).

속성명은 dataclass 필드 그대로(``kind``/``given``/``then``) — 옛 ``when_``/``then_``
밑줄 회피는 "Cypher 예약어" 근거가 실측으로 부정되어 폐기했다.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Example
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER

_UPSERT_QUERY = f"""
UNWIND $items AS item
MERGE (e:ANALYZER_OWNED:EXAMPLE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET e:ANALYZER_OWNED,
    e.kind = item.kind,
    e.given = item.given,
    e.then = item.then,
    e.graph_owner = '{ANALYZER_GRAPH_OWNER}'
"""


async def upsert_examples(client: Neo4jClient, examples: Iterable[Example]) -> None:
    """EXAMPLE 노드 MERGE — given/then 은 파이썬 leaf 문자열 배열 + kind."""
    items = [
        {
            "id": e.id,
            "kind": e.kind,
            "given": list(e.given),
            "then": list(e.then),
        }
        for e in examples
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
