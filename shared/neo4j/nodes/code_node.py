"""코드 노드 Cypher (함수/메서드/프로시저/구문/구조).

분석 단위. Framework / DBMS 동일 노드. DBMS 의 nested range 는 ``parent_id`` 필드로
표현 + ``edges/parent_of.py`` 가 PARENT_OF 엣지 박음.

라벨 = 노드의 antlr type 하나 (``CodeNode.label`` — :METHOD/:FUNCTION/:PROCEDURE/:SELECT/:IF …).
저장 = **종류에 의미있는 속성만**(spec 020, 빈값 미저장 `SET n += item`):
- ``signature`` 는 저장 안 함 (루틴은 ``params``/``return_type`` 가 대체 — 단 필드는
  call_linker·LLM 컨텍스트가 써서 유지). ``comment``+``annotations`` → 단일 ``notes``.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import CodeNode
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.stereotype_label import apply_stereotype_labels
from shared.neo4j.props import join_notes, nonempty


# 빈값 미저장 — item 에 담긴(비어있지 않은) 키만 SET. 구문 노드는 params/return_type 등을
# 자연히 안 가짐. label 은 ``CodeNode.label`` 통제값(주입 안전).
def _merge_query(label: str) -> str:
    return f"""
UNWIND $items AS item
MERGE (node:{label} {{id: item.id}})
SET node += item
RETURN count(node) AS created
"""


# 의미 분석 결과 갱신 (analyze_code_step). table_insights 는 노드에 안 박는다 —
# enrich_table_description_step 이 Table/Column.analyzed_description 으로 분배(제자리, spec 020).
SET_SUMMARY_QUERY = """
UNWIND $items AS item
MATCH (node {id: item.id})
SET node += item
"""


def to_item(fn: CodeNode) -> dict:
    """저장 속성 (비어있지 않은 것만). signature 미저장, comment+annotations→notes."""
    return nonempty({
        "id": fn.id,
        "name": fn.name,
        "file_path": fn.file_path,
        "start_line": fn.start_line,
        "end_line": fn.end_line,
        "return_type": fn.return_type,
        "params": fn.params,
        "notes": join_notes(fn.comment, fn.annotations),
    })


def to_summary_item(fn: CodeNode) -> dict:
    """3 분할 분석 결과 → 적재 item (비어있지 않은 것만). table_insights 는 컬럼/엣지로 분배."""
    return nonempty({
        "id": fn.id,
        "summary": fn.summary,
        "stereotype": fn.stereotype,
        "logical_name": fn.logical_name,
        "code_text": fn.code_text,
    })


async def upsert_code_nodes(client: Neo4jClient, code_nodes: Iterable[CodeNode]) -> None:
    """노드의 antlr type 라벨(``fn.label``)별로 묶어 MERGE — ``:METHOD``/``:FUNCTION``/``:PROCEDURE``/``:SELECT`` …"""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for fn in code_nodes:
        by_label[fn.label].append(to_item(fn))
    for label, items in by_label.items():
        await client.run_batch_unwind(_merge_query(label), items)


async def update_summaries(client: Neo4jClient, code_nodes: Iterable[CodeNode]) -> None:
    """analyze_code 결과 (summary / stereotype / logical_name / code_text) 일괄 갱신.

    stereotype 은 속성 + **라벨**로 부여(spec 032) — 소비처가 역할로 필터/색칠하기 쉽게.
    """
    analyzed = [f for f in code_nodes if f.summary is not None]
    await client.run_batch_unwind(SET_SUMMARY_QUERY, [to_summary_item(f) for f in analyzed])
    await apply_stereotype_labels(client, [(f.id, f.stereotype or "") for f in analyzed])
