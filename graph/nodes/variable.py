"""변수 노드 Cypher — 필드 / 멤버 / 전역변수 / 파라미터.

라벨 = 자기 antlr type 하나 (``Variable.label`` — :FIELD/:MEMBER/:GLOBAL_VARIABLE/:VARIABLE/:PARAMETER).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Variable
from graph.node_upsert import upsert_by_node_label
from graph.neo4j_client import Neo4jClient
from graph.node_upsert import join_notes, nonempty


# MERGE 쿼리·라벨 그룹핑은 ``graph/node_upsert.py`` 단일 정의처 (spec 046 FR-004).


def to_item(v: Variable) -> dict:
    return nonempty({
        "id": v.id,
        "owner_id": v.owner_id,   # 소속 모듈 (spec 047 FR-007 — 소비자가 id 를 뜯지 않게)
        "name": v.name,
        "file_path": v.file_path,
        "start_line": v.start_line,
        "end_line": v.end_line,
        "var_type": v.var_type,
        "init_value": v.init_value,
        "code_text": v.code_text,
        "notes": join_notes(v.comment, v.annotations),
    })


async def upsert_variables(client: Neo4jClient, variables: Iterable[Variable]) -> None:
    """antlr type 라벨(``v.label``)별로 묶어 MERGE — ``:FIELD``/``:MEMBER``/``:GLOBAL_VARIABLE``/``:VARIABLE``/``:PARAMETER``."""
    await upsert_by_node_label(client, variables, to_item)
