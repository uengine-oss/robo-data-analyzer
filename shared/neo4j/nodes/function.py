"""Function 노드 Cypher.

분석 단위. Framework / DBMS 동일 노드 (분기 0개). DBMS 의 nested range 는
``parent_id`` 필드로 표현 + ``edges/parent_of.py`` 가 PARENT_OF 엣지 박음.

3 분할 LLM 결과 모두 Neo4j 에 박힘:
- summary / stereotype / actor / logical_name  → 단일 필드
- table_insights / questions / rules            → JSON 문자열 (Neo4j map 미지원 → str)
"""
from __future__ import annotations

import json
from typing import Iterable

from data_models.graph_nodes import Function
from shared.neo4j.neo4j_client import Neo4jClient


# 식별자 (function_id) + 위치만 MERGE — 의미 필드는 별도 쿼리로 갱신
MERGE_QUERY = """
UNWIND $items AS item
MERGE (f:Function {function_id: item.function_id})
SET f.signature = item.signature,
    f.file_path = item.file_path,
    f.start_line = item.start_line,
    f.end_line = item.end_line,
    f.parent_id = item.parent_id
RETURN count(f) AS created
"""

# 의미 분석 결과 갱신 (analyze_function_step) — 3분할 결과 모두 박음.
# Neo4j 는 map property 미지원 → table_insights / questions / rules 는 JSON 문자열.
SET_SUMMARY_QUERY = """
UNWIND $items AS item
MATCH (f:Function {function_id: item.function_id})
SET f.summary = item.summary,
    f.stereotype = item.stereotype,
    f.actor = item.actor,
    f.logical_name = item.logical_name,
    f.table_insights_json = item.table_insights_json,
    f.questions_json = item.questions_json,
    f.rules_json = item.rules_json
"""


def to_item(fn: Function) -> dict:
    return {
        "function_id": fn.function_id,
        "signature": fn.signature,
        "file_path": fn.file_path,
        "start_line": fn.start_line,
        "end_line": fn.end_line,
        "parent_id": fn.parent_id,
    }


def to_summary_item(fn: Function) -> dict:
    """3 분할 결과 → Neo4j 적재 item.

    ``fn.extra`` 의 ``table_insights`` / ``questions`` / ``rules`` 도 JSON 으로 박음.
    (Neo4j 는 nested map property 미지원 → ``_json`` suffix 컨벤션)
    """
    extra = fn.extra or {}
    return {
        "function_id": fn.function_id,
        "summary": fn.summary,
        "stereotype": fn.stereotype,
        "actor": fn.actor,
        "logical_name": fn.logical_name,
        "table_insights_json": json.dumps(
            extra.get("table_insights", []), ensure_ascii=False,
        ),
        "questions_json": json.dumps(
            extra.get("questions", []), ensure_ascii=False,
        ),
        "rules_json": json.dumps(
            extra.get("rules", []), ensure_ascii=False,
        ),
    }


async def upsert_functions(client: Neo4jClient, functions: Iterable[Function]) -> None:
    items = [to_item(f) for f in functions]
    await client.run_batch_unwind(MERGE_QUERY, items)


async def update_summaries(client: Neo4jClient, functions: Iterable[Function]) -> None:
    """analyze_function 결과 (summary / stereotype / actor / logical_name + 3분할 extra) 일괄 갱신."""
    items = [to_summary_item(f) for f in functions if f.summary is not None]
    await client.run_batch_unwind(SET_SUMMARY_QUERY, items)
