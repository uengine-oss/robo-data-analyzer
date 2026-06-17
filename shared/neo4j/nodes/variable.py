"""VARIABLE 노드 Cypher — 필드 / 멤버 / 전역변수.

라벨: ``:VARIABLE`` + Framework sub-label (``:VARIABLE:FIELD`` 등 — ``Variable.sub_label``).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import Variable
from shared.neo4j.neo4j_client import Neo4jClient


def _merge_query(label: str) -> str:
    """label 은 ``Variable.sub_label`` 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MERGE (v:{label} {{id: item.id}})
SET v.name = item.name,
    v.file_path = item.file_path,
    v.start_line = item.start_line,
    v.end_line = item.end_line,
    v.var_type = item.var_type,
    v.comment = item.comment,
    v.annotations = item.annotations,
    v.init_value = item.init_value
RETURN count(v) AS created
"""


def to_item(v: Variable) -> dict:
    return {
        "id": v.id,
        "name": v.name,
        "file_path": v.file_path,
        "start_line": v.start_line,
        "end_line": v.end_line,
        "var_type": v.var_type,
        "comment": v.comment,
        "annotations": v.annotations,
        "init_value": v.init_value,
    }


async def upsert_variables(client: Neo4jClient, variables: Iterable[Variable]) -> None:
    """sub_label 별로 묶어 MERGE (멀티라벨 ``:VARIABLE:FIELD`` 등)."""
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for v in variables:
        by_sub[v.sub_label].append(to_item(v))
    for sub, items in by_sub.items():
        label = f"VARIABLE:{sub}" if sub else "VARIABLE"
        await client.run_batch_unwind(_merge_query(label), items)
