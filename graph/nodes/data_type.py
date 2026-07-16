"""데이터 타입 노드 Cypher — C struct / union / enum (Java enum 포함).

라벨 = 자기 antlr type 하나 (``DataType.label`` — :STRUCT / :UNION / :ENUM).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import DataType
from graph.node_upsert import upsert_by_node_label
from graph.neo4j_client import Neo4jClient
from graph.node_upsert import join_notes, nonempty


# MERGE 쿼리·라벨 그룹핑은 ``graph/node_upsert.py`` 단일 정의처 (spec 046 FR-004).


def to_item(d: DataType) -> dict:
    return nonempty({
        "id": d.id,
        "owner_id": d.owner_id,   # 소속 모듈 (spec 047 FR-007 — 소비자가 id 를 뜯지 않게)
        "name": d.name,
        "file_path": d.file_path,
        "start_line": d.start_line,
        "end_line": d.end_line,
        "code_text": d.code_text,
        "notes": join_notes(d.comment),
    })


async def upsert_data_types(client: Neo4jClient, data_types: Iterable[DataType]) -> None:
    """antlr type 라벨(``d.label``)별로 묶어 MERGE — ``:STRUCT`` / ``:UNION`` / ``:ENUM``."""
    await upsert_by_node_label(client, data_types, to_item)
