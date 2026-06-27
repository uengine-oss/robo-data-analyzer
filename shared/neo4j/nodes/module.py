"""컨테이너(그릇) 노드 Cypher — 코드 노드들을 담는 단위.

라벨 = 자기 antlr type 하나 (``Module.label`` — reader 가 ``node["type"]`` 직결): 파일=``:FILE``,
Java 클래스=``:CLASS``, 인터페이스=``:INTERFACE``. 역할 라벨(MODULE) 폐기 — 소비처는 그릇을
**타입 집합{FILE,CLASS,INTERFACE,…}**으로 식별(navigator). (외부 호출 stub 만 종류미상이라
``:MODULE:EXTERNAL`` — calls.py.)
summary / stereotype / logical_name 은 summarize_module_step 이 갱신.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import Module
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.props import join_notes, nonempty


def _merge_query(label: str) -> str:
    """빈값 미저장 (`SET m += item`) — 빈 summary 등은 키가 없어 기존값 보존. 라벨 통제값(주입 안전)."""
    return f"""
UNWIND $items AS item
MERGE (m:{label} {{id: item.id}})
SET m += item
RETURN count(m) AS created
"""


def to_item(mod: Module) -> dict:
    return nonempty({
        "id": mod.id,
        "name": mod.name,
        "file_path": mod.file_path,
        "start_line": mod.start_line,
        "end_line": mod.end_line,
        "notes": join_notes(mod.comment, mod.annotations),
        "summary": mod.summary,
        "stereotype": mod.stereotype,
        "logical_name": mod.logical_name,
    })


async def upsert_modules(client: Neo4jClient, modules: Iterable[Module]) -> None:
    """antlr type 라벨(``m.label``)별로 묶어 MERGE — ``:FILE`` / ``:CLASS`` / ``:INTERFACE`` 등.

    옛 ``:MODULE`` 폐기 — 소비처는 그릇을 타입 집합{FILE,CLASS,…}으로 식별(navigator).
    """
    by_label: dict[str, list[dict]] = defaultdict(list)
    for m in modules:
        by_label[m.label].append(to_item(m))
    for label, items in by_label.items():
        await client.run_batch_unwind(_merge_query(label), items)
