"""REFERENCES 엣지 — Function → Variable / Constant.

target_label(=Neo4j 라벨 VARIABLE/CONSTANT)로 MATCH 정밀화 — 키는 모든 노드 공통 ``id``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_edges import References
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import REFERENCES


def _merge_query(label: str) -> str:
    """label 은 ``References.target_label`` 통제값(VARIABLE/CONSTANT) — 주입 안전."""
    return f"""
UNWIND $items AS item
MATCH (node {{id: item.source_id}})
MATCH (t:{label} {{id: item.target_id}})
MERGE (node)-[r:{REFERENCES}]->(t)
SET r.evidence_line = item.evidence_line
"""


async def upsert_references(client: Neo4jClient, edges: Iterable[References]) -> None:
    """target_label 별로 묶어 MERGE (라벨 정밀 MATCH, 키는 id)."""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_label[e.target_label].append({
            "source_id": e.source_id,
            "target_id": e.target_id,
            "evidence_line": e.evidence_line,
        })
    for target_label, items in by_label.items():
        await client.run_batch_unwind(_merge_query(target_label), items)
