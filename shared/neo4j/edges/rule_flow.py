"""Rule 간 흐름 엣지 — NEXT(메인 순서) / BRANCH(분기).

NEXT·BRANCH 는 Cypher 가 동일 (rel 타입만 다름) — 한 파일에 통합 (중복 회피).
Rule 노드는 함수 간 공유되므로 같은 함수의 HAS_RULE `local_rule_id` 로 양끝 Rule 을
특정하고, 엣지 자체는 ``function_id`` 로 qualify.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import Branch, Next
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import BRANCH, HAS_RULE, NEXT


def _flow_query(rel: str) -> str:
    """rel(NEXT / BRANCH) 별 Rule→Rule MERGE 쿼리."""
    return f"""
UNWIND $items AS item
MATCH (f:FUNCTION {{function_id: item.function_id}})
      -[:{HAS_RULE} {{local_rule_id: item.from_rule_id}}]->(r1:RULE)
MATCH (f)-[:{HAS_RULE} {{local_rule_id: item.to_rule_id}}]->(r2:RULE)
MERGE (r1)-[:{rel} {{function_id: item.function_id}}]->(r2)
"""


_NEXT_QUERY = _flow_query(NEXT)
_BRANCH_QUERY = _flow_query(BRANCH)


def _items(edges) -> list[dict]:
    return [
        {
            "function_id": e.function_id,
            "from_rule_id": e.from_rule_id,
            "to_rule_id": e.to_rule_id,
        }
        for e in edges
    ]


async def upsert_next(client: Neo4jClient, edges: Iterable[Next]) -> None:
    """Rule → Rule NEXT (메인 흐름 순서)."""
    await client.run_batch_unwind(_NEXT_QUERY, _items(edges))


async def upsert_branch(client: Neo4jClient, edges: Iterable[Branch]) -> None:
    """Rule → Rule BRANCH (분기)."""
    await client.run_batch_unwind(_BRANCH_QUERY, _items(edges))
