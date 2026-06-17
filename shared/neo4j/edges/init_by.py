"""INIT_BY 엣지 — 변수/상수 → 출처 노드 (정의 시 참조하는 다른 변수·상수).

from_label · source_label(=Neo4j 라벨 VARIABLE/CONSTANT)로 MATCH 정밀화 —
키는 모든 노드 공통 ``id``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_edges import InitBy
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import INIT_BY


def _merge_query(from_label: str, source_label: str) -> str:
    """라벨은 ``InitBy.from_label`` / ``source_label`` 통제값 — 주입 안전. 키는 id."""
    return f"""
UNWIND $items AS item
MATCH (f:{from_label} {{id: item.from_id}})
MATCH (s:{source_label} {{id: item.source_id}})
MERGE (f)-[:{INIT_BY}]->(s)
"""


async def upsert_init_by(client: Neo4jClient, edges: Iterable[InitBy]) -> None:
    """(from_label, source_label) 조합별로 묶어 MERGE — 4가지 조합 가능."""
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in edges:
        by_pair[(e.from_label, e.source_label)].append({
            "from_id": e.from_id,
            "source_id": e.source_id,
        })
    for (from_label, source_label), items in by_pair.items():
        await client.run_batch_unwind(_merge_query(from_label, source_label), items)
