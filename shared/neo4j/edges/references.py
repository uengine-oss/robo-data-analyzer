"""REFERENCES 엣지 — Function → Variable / Constant.

target_label 별 분기 — Variable 은 variable_id, Constant 는 constant_id 키로 MATCH.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_edges import References
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import REFERENCES


# target_label → (Neo4j 라벨, 키 속성)
_TARGET: dict[str, tuple[str, str]] = {
    "VARIABLE": ("VARIABLE", "variable_id"),
    "CONSTANT": ("CONSTANT", "constant_id"),
}


def _merge_query(label: str, key: str) -> str:
    """label / key 는 ``_TARGET`` 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MATCH (f:FUNCTION {{function_id: item.function_id}})
MATCH (t:{label} {{{key}: item.target_id}})
MERGE (f)-[r:{REFERENCES}]->(t)
SET r.evidence_line = item.evidence_line
"""


async def upsert_references(client: Neo4jClient, edges: Iterable[References]) -> None:
    """target_label 별로 묶어 MERGE."""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_label[e.target_label].append({
            "function_id": e.function_id,
            "target_id": e.target_id,
            "evidence_line": e.evidence_line,
        })
    for target_label, items in by_label.items():
        spec = _TARGET.get(target_label)
        if spec is None:
            continue
        label, key = spec
        await client.run_batch_unwind(_merge_query(label, key), items)
