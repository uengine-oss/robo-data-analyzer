"""DATA_TYPE 노드 Cypher — C struct / union / enum (Java enum 포함).

라벨: ``:DATA_TYPE`` + Framework sub-label (있으면 — ``DataType.sub_label``).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import DataType
from shared.neo4j.neo4j_client import Neo4jClient


def _merge_query(label: str) -> str:
    """label 은 ``DataType.sub_label`` 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MERGE (d:{label} {{datatype_id: item.datatype_id}})
SET d.module_id = item.module_id,
    d.name = item.name,
    d.file_path = item.file_path,
    d.start_line = item.start_line,
    d.end_line = item.end_line,
    d.comment = item.comment
RETURN count(d) AS created
"""


def to_item(d: DataType) -> dict:
    return {
        "datatype_id": d.datatype_id,
        "module_id": d.module_id,
        "name": d.name,
        "file_path": d.file_path,
        "start_line": d.start_line,
        "end_line": d.end_line,
        "comment": d.comment,
    }


async def upsert_data_types(client: Neo4jClient, data_types: Iterable[DataType]) -> None:
    """sub_label 별로 묶어 MERGE."""
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for d in data_types:
        by_sub[d.sub_label].append(to_item(d))
    for sub, items in by_sub.items():
        label = f"DATA_TYPE:{sub}" if sub else "DATA_TYPE"
        await client.run_batch_unwind(_merge_query(label), items)
