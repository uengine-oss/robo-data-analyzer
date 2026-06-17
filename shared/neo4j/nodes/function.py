"""FUNCTION 노드 Cypher.

분석 단위. Framework / DBMS 동일 노드. DBMS 의 nested range 는 ``parent_id`` 필드로
표현 + ``edges/parent_of.py`` 가 PARENT_OF 엣지 박음.

라벨: ``:FUNCTION`` + Framework sub-label (``:FUNCTION:METHOD`` 등 — ``Function.sub_label``).
sub_label 은 reader 가 ontology 로 도출 — 저장 코드는 그 값만 본다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import Function
from shared.neo4j.neo4j_client import Neo4jClient


def _merge_query(label: str) -> str:
    """주어진 라벨(예: ``FUNCTION`` / ``FUNCTION:METHOD``)로 MERGE 쿼리 생성.

    label 은 ``Function.sub_label`` 에서 온 통제된 값 — 임의 입력 아님 (주입 안전).
    """
    return f"""
UNWIND $items AS item
MERGE (node:{label} {{id: item.id}})
SET node.name = item.name,
    node.signature = item.signature,
    node.file_path = item.file_path,
    node.start_line = item.start_line,
    node.end_line = item.end_line,
    node.annotations = item.annotations,
    node.comment = item.comment,
    node.return_type = item.return_type,
    node.params = item.params
RETURN count(node) AS created
"""


# 의미 분석 결과 갱신 (analyze_function_step) — 3분할 결과.
# table_insights 는 JSON 문자열로 FUNCTION 에. rules / examples / questions 는
# M12 에서 Rule/Example/Question 그래프 노드로 분리 — 여기서 안 박음.
SET_SUMMARY_QUERY = """
UNWIND $items AS item
MATCH (node {id: item.id})
SET node.summary = item.summary,
    node.stereotype = item.stereotype,
    node.logical_name = item.logical_name,
    node.table_insights_json = item.table_insights_json,
    node.code_text = item.code_text
"""


def to_item(fn: Function) -> dict:
    return {
        "id": fn.id,
        "name": fn.name,
        "signature": fn.signature,
        "file_path": fn.file_path,
        "start_line": fn.start_line,
        "end_line": fn.end_line,
        "annotations": fn.annotations,
        "comment": fn.comment,
        "return_type": fn.return_type,
        "params": fn.params,
    }


def to_summary_item(fn: Function) -> dict:
    """3 분할 결과 → Neo4j 적재 item.

    ``fn.extra["table_insights"]`` 는 JSON 으로 박음 (Neo4j nested map 미지원 →
    ``_json`` suffix 컨벤션). rules / examples / questions 는 M12 EM 빌더가
    Rule/Example/Question 노드로 분리 — 여기서 안 박는다.
    """
    extra = fn.extra or {}
    return {
        "id": fn.id,
        "summary": fn.summary,
        "stereotype": fn.stereotype,
        "logical_name": fn.logical_name,
        "table_insights_json": json.dumps(extra.get("table_insights", []), ensure_ascii=False),
        "code_text": fn.code_text,
    }


async def upsert_functions(client: Neo4jClient, functions: Iterable[Function]) -> None:
    """(primary_label, sub_label) 별로 묶어 라벨 박힌 MERGE.

    Framework: primary="FUNCTION" + ontology sub-label → ``:FUNCTION:METHOD`` (역할 그룹).
    DBMS: primary=antlr type, sub 없음 → ``:SELECT``/``:PROCEDURE``/``:IF`` … (spec 016).
    """
    by_label: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for fn in functions:
        by_label[(fn.primary_label or "FUNCTION", fn.sub_label)].append(to_item(fn))
    for (primary, sub), items in by_label.items():
        label = f"{primary}:{sub}" if sub else primary
        await client.run_batch_unwind(_merge_query(label), items)


async def update_summaries(client: Neo4jClient, functions: Iterable[Function]) -> None:
    """analyze_function 결과 (summary / stereotype / logical_name + 3분할 extra) 일괄 갱신."""
    items = [to_summary_item(f) for f in functions if f.summary is not None]
    await client.run_batch_unwind(SET_SUMMARY_QUERY, items)
