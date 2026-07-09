"""RULE 노드 Cypher — 비즈니스 규칙 (Example Mapping).

같은 statement 는 ``id``(=정규화 해시) 가 같아 1 노드 공유 (MERGE) — 코드노드 간 규칙 공유.
per-function 정보(coupled_domains)는 노드가 아닌 HAS_RULE 엣지 속성에.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Rule
from shared.neo4j.neo4j_client import Neo4jClient

_UPSERT_QUERY = """
UNWIND $items AS item
MERGE (r:RULE {id: item.id})
SET r.statement = item.statement
"""


async def upsert_rules(client: Neo4jClient, rules: Iterable[Rule]) -> None:
    """RULE 노드 MERGE — id(=해시) 공유 (동일 statement = 1 노드)."""
    items = [{"id": r.id, "statement": r.statement} for r in rules]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
