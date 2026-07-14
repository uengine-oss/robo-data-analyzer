"""상수 노드 Cypher — ``#define`` / ``static final`` / ``CONSTANT_FIELD`` 등.

라벨 = 자기 antlr type 하나 (``Constant.label`` — :DEFINE / :CONSTANT_FIELD).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Constant
from shared.neo4j.merge import upsert_by_node_label
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.props import join_notes, nonempty


# MERGE 쿼리·라벨 그룹핑은 ``shared/neo4j/merge.py`` 단일 정의처 (spec 046 FR-004).


def to_item(c: Constant) -> dict:
    return nonempty({
        "id": c.id,
        "owner_id": c.owner_id,   # 소속 모듈 (spec 047 FR-007 — 소비자가 id 를 뜯지 않게)
        "name": c.name,
        "file_path": c.file_path,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "value": c.value,
        "var_type": c.var_type,
        "code_text": c.code_text,
        "notes": join_notes(c.comment),
    })


async def upsert_constants(client: Neo4jClient, constants: Iterable[Constant]) -> None:
    """antlr type 라벨(``c.label``)별로 묶어 MERGE — ``:DEFINE`` / ``:CONSTANT_FIELD``."""
    await upsert_by_node_label(client, constants, to_item)
