"""상수 노드 Cypher — ``#define`` / ``static final`` / ``CONSTANT_FIELD`` 등.

라벨 = 자기 antlr type 하나 (``Constant.label`` — :DEFINE / :CONSTANT_FIELD).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import Constant
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.props import join_notes, nonempty


def _merge_query(label: str) -> str:
    """빈값 미저장 (`SET c += item`). label 은 ``Constant.label``(antlr type) 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MERGE (c:{label} {{id: item.id}})
SET c += item
RETURN count(c) AS created
"""


def to_item(c: Constant) -> dict:
    return nonempty({
        "id": c.id,
        "name": c.name,
        "file_path": c.file_path,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "value": c.value,
        "var_type": c.var_type,
        "notes": join_notes(c.comment),
    })


async def upsert_constants(client: Neo4jClient, constants: Iterable[Constant]) -> None:
    """antlr type 라벨(``c.label``)별로 묶어 MERGE — ``:DEFINE`` / ``:CONSTANT_FIELD``."""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for c in constants:
        by_label[c.label].append(to_item(c))
    for label, items in by_label.items():
        await client.run_batch_unwind(_merge_query(label), items)
