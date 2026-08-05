"""RULE 노드 Cypher — 구조화 비즈니스 규칙 (Example Mapping, spec 116).

id = ``<function id>::R<anchor>`` — anchor 좌표가 정체성이라 statement 해시 공유는 없다.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Rule
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER

_UPSERT_QUERY = f"""
UNWIND $items AS item
MERGE (r:RULE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
SET r:ANALYZER_OWNED,
    r.rule_id = item.rule_id,
    r.anchor_line = item.anchor_line,
    r.cond = item.cond,
    r.then = item.then,
    r.nl = item.nl,
    r.when = item.when,
    r.graph_owner = '{ANALYZER_GRAPH_OWNER}'
"""


async def upsert_rules(client: Neo4jClient, rules: Iterable[Rule]) -> None:
    """RULE 노드 MERGE — 구조화 속성 전체 적재 (then 은 문자열 배열 속성)."""
    items = [
        {
            "id": r.id,
            "rule_id": r.rule_id,
            "anchor_line": r.anchor_line,
            "cond": r.cond,
            "then": list(r.then),
            "nl": r.nl,
            "when": r.when,
        }
        for r in rules
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
