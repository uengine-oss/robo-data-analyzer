"""INIT_BY 엣지 — Variable → 출처 노드 (초기화식이 참조하는 변수·상수).

source_label 별 분기 — Variable 은 variable_id, Constant 는 constant_id 키로 MATCH.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_edges import InitBy
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import INIT_BY


# source_label → (Neo4j 라벨, 키 속성)
_SOURCE: dict[str, tuple[str, str]] = {
    "VARIABLE": ("VARIABLE", "variable_id"),
    "CONSTANT": ("CONSTANT", "constant_id"),
}


def _merge_query(label: str, key: str) -> str:
    """label / key 는 ``_SOURCE`` 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MATCH (v:VARIABLE {{variable_id: item.variable_id}})
MATCH (s:{label} {{{key}: item.source_id}})
MERGE (v)-[:{INIT_BY}]->(s)
"""


async def upsert_init_by(client: Neo4jClient, edges: Iterable[InitBy]) -> None:
    """source_label 별로 묶어 MERGE."""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_label[e.source_label].append({
            "variable_id": e.variable_id,
            "source_id": e.source_id,
        })
    for source_label, items in by_label.items():
        spec = _SOURCE.get(source_label)
        if spec is None:
            continue
        label, key = spec
        await client.run_batch_unwind(_merge_query(label, key), items)
