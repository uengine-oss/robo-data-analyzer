"""INIT_BY 엣지 — 변수/상수 → 출처 노드 (정의 시 참조하는 다른 변수·상수).

from_label · source_label 별 분기 — VARIABLE 은 variable_id, CONSTANT 는
constant_id 키로 MATCH.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_edges import InitBy
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import INIT_BY


# 라벨 → 키 속성 (VARIABLE / CONSTANT 양쪽 다 from·source 둘 다 가능)
_KEY_OF: dict[str, str] = {
    "VARIABLE": "variable_id",
    "CONSTANT": "constant_id",
}


def _merge_query(from_label: str, from_key: str, source_label: str, source_key: str) -> str:
    """라벨/키는 ``_KEY_OF`` 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MATCH (f:{from_label} {{{from_key}: item.from_id}})
MATCH (s:{source_label} {{{source_key}: item.source_id}})
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
        from_key = _KEY_OF.get(from_label)
        source_key = _KEY_OF.get(source_label)
        if from_key is None or source_key is None:
            continue
        await client.run_batch_unwind(
            _merge_query(from_label, from_key, source_label, source_key),
            items,
        )
