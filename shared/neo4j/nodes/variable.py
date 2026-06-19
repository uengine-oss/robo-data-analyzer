"""변수 노드 Cypher — 필드 / 멤버 / 전역변수 / 파라미터.

라벨 = 자기 antlr type 하나 (``Variable.label`` — :FIELD/:MEMBER/:GLOBAL_VARIABLE/:VARIABLE/:PARAMETER).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import Variable
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.props import join_notes, nonempty


def _merge_query(label: str) -> str:
    """빈값 미저장 (`SET v += item`). label 은 ``Variable.label``(antlr type) 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MERGE (v:{label} {{id: item.id}})
SET v += item
RETURN count(v) AS created
"""


def to_item(v: Variable) -> dict:
    return nonempty({
        "id": v.id,
        "name": v.name,
        "file_path": v.file_path,
        "start_line": v.start_line,
        "end_line": v.end_line,
        "var_type": v.var_type,
        "init_value": v.init_value,
        "notes": join_notes(v.comment, v.annotations),
    })


async def upsert_variables(client: Neo4jClient, variables: Iterable[Variable]) -> None:
    """antlr type 라벨(``v.label``)별로 묶어 MERGE — ``:FIELD``/``:MEMBER``/``:GLOBAL_VARIABLE``/``:VARIABLE``/``:PARAMETER``."""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for v in variables:
        by_label[v.label].append(to_item(v))
    for label, items in by_label.items():
        await client.run_batch_unwind(_merge_query(label), items)
