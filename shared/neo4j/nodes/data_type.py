"""데이터 타입 노드 Cypher — C struct / union / enum (Java enum 포함).

라벨 = 자기 antlr type 하나 (``DataType.label`` — :STRUCT / :UNION / :ENUM).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import DataType
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.props import join_notes, nonempty


def _merge_query(label: str) -> str:
    """빈값 미저장 (`SET d += item`). label 은 ``DataType.label``(antlr type) 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MERGE (d:{label} {{id: item.id}})
SET d += item
RETURN count(d) AS created
"""


def to_item(d: DataType) -> dict:
    return nonempty({
        "id": d.id,
        "name": d.name,
        "file_path": d.file_path,
        "start_line": d.start_line,
        "end_line": d.end_line,
        "notes": join_notes(d.comment),
    })


async def upsert_data_types(client: Neo4jClient, data_types: Iterable[DataType]) -> None:
    """antlr type 라벨(``d.label``)별로 묶어 MERGE — ``:STRUCT`` / ``:UNION`` / ``:ENUM``."""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for d in data_types:
        by_label[d.label].append(to_item(d))
    for label, items in by_label.items():
        await client.run_batch_unwind(_merge_query(label), items)
