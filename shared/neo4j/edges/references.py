"""REFERENCES 엣지 — CodeNode → Variable / Constant.

MATCH 는 모든 노드 공통 ``id`` 로만 (라벨 무관 — 변수/상수 라벨이 antlr type 열린집합).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import References
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import REFERENCES


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (node {{id: item.source_id}})
MATCH (t {{id: item.target_id}})
MERGE (node)-[r:{REFERENCES}]->(t)
SET r.evidence_line = item.evidence_line
"""


async def upsert_references(client: Neo4jClient, edges: Iterable[References]) -> None:
    """id 무라벨 MATCH 로 MERGE."""
    items = [
        {"source_id": e.source_id, "target_id": e.target_id, "evidence_line": e.evidence_line}
        for e in edges
    ]
    await client.run_batch_unwind(MERGE_QUERY, items)
