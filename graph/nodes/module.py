"""컨테이너(그릇) 노드 Cypher — 코드 노드들을 담는 단위.

라벨 = 자기 antlr type 하나 (``Module.label`` — reader 가 ``node["type"]`` 직결): 파일=``:FILE``,
Java 클래스=``:CLASS``, 인터페이스=``:INTERFACE``. 역할 라벨(MODULE) 폐기 — 소비처는 그릇을
**타입 집합{FILE,CLASS,INTERFACE,…}**으로 식별(navigator). (외부 호출 stub 만 종류미상이라
``:MODULE`` + ``external`` 속성 — calls.py.)
summary / stereotype / logical_name 은 summarize_module_step 이 갱신. stereotype 은 속성으로만
저장한다(라벨 아님 — ``code_node.update_summaries`` docstring).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Module
from graph.node_upsert import upsert_by_node_label
from graph.neo4j_client import Neo4jClient
from graph.node_upsert import join_notes, nonempty


# MERGE 쿼리·라벨 그룹핑은 ``graph/node_upsert.py`` 단일 정의처 (spec 046 FR-004).


def to_item(mod: Module) -> dict:
    return nonempty({
        "id": mod.id,
        "type": mod.label,
        "name": mod.name,
        "file_path": mod.file_path,
        "start_line": mod.start_line,
        "end_line": mod.end_line,
        "notes": join_notes(mod.comment, mod.annotations),
        "code_text": mod.code_text,
        "summary": mod.summary,
        "stereotype": mod.stereotype,
        "logical_name": mod.logical_name,
    })


async def upsert_modules(client: Neo4jClient, modules: Iterable[Module]) -> None:
    """antlr type 라벨(``m.label``)별로 묶어 MERGE — ``:FILE`` / ``:CLASS`` / ``:INTERFACE`` 등.

    옛 ``:MODULE`` 폐기 — 소비처는 그릇을 타입 집합{FILE,CLASS,…}으로 식별(navigator).
    """
    await upsert_by_node_label(client, list(modules), to_item)
