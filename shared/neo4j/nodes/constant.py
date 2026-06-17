"""CONSTANT 노드 Cypher — ``#define`` / ``static final`` / ``CONSTANT_FIELD`` 등.

라벨: ``:CONSTANT`` + Framework sub-label (있으면 — ``Constant.sub_label``).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import Constant
from shared.neo4j.neo4j_client import Neo4jClient


def _merge_query(label: str) -> str:
    """label 은 ``Constant.sub_label`` 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MERGE (c:{label} {{id: item.id}})
SET c.name = item.name,
    c.file_path = item.file_path,
    c.start_line = item.start_line,
    c.end_line = item.end_line,
    c.value = item.value,
    c.var_type = item.var_type,
    c.comment = item.comment
RETURN count(c) AS created
"""


def to_item(c: Constant) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "file_path": c.file_path,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "value": c.value,
        "var_type": c.var_type,
        "comment": c.comment,
    }


async def upsert_constants(client: Neo4jClient, constants: Iterable[Constant]) -> None:
    """sub_label 별로 묶어 MERGE."""
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for c in constants:
        by_sub[c.sub_label].append(to_item(c))
    for sub, items in by_sub.items():
        label = f"CONSTANT:{sub}" if sub else "CONSTANT"
        await client.run_batch_unwind(_merge_query(label), items)
