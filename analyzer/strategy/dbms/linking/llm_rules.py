"""DBMS LLM 호출 헬퍼 — PromptRegistry 기반.

모든 DBMS LLM 호출은 PromptRegistry.execute_raw()를 통해 수행.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from util.prompt_registry import prompts


def analyze_code(code: str, context: str, ranges: list, count: int, api_key: str, locale: str) -> Dict[str, Any]:
    """코드 분석 → rules/dbms/node_analysis.yaml"""
    inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return prompts.execute_raw("dbms", "node_analysis", inputs, api_key)


def analyze_dml_tables(code: str, context: str, ranges: list, api_key: str, locale: str) -> Dict[str, Any]:
    """DML 테이블 분석 → rules/dbms/dml_extract.yaml"""
    inputs = {"code": code, "ranges": ranges, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return prompts.execute_raw("dbms", "dml_extract", inputs, api_key)


def analyze_summary_only(summaries: dict, api_key: str, locale: str, previous_summary: str = "") -> Dict[str, Any]:
    """프로시저/함수 전체 요약 → rules/dbms/unit_analysis.yaml"""
    return prompts.execute_raw(
        "dbms", "unit_analysis",
        {"summaries": summaries, "locale": locale, "previous_summary": previous_summary},
        api_key,
    )


def summarize_table_metadata(
    table_name: str, table_sentences: list, column_sentences: dict,
    column_metadata: dict, api_key: str, locale: str,
) -> Dict[str, Any]:
    """테이블 메타데이터 요약 → rules/dbms/table_summary.yaml"""
    return prompts.execute_raw(
        "dbms", "table_summary",
        {"table_name": table_name, "table_sentences": table_sentences,
         "column_sentences": column_sentences, "column_metadata": column_metadata, "locale": locale},
        api_key,
    )


def analyze_variables(declaration_code: str, api_key: str, locale: str) -> Dict[str, Any]:
    """변수 분석 → rules/dbms/variable_analysis.yaml"""
    return prompts.execute_raw("dbms", "variable_analysis", {"declaration_code": declaration_code, "locale": locale}, api_key)


def extract_node_context(
    skeleton_code: str, ancestor_context: str, sibling_code: str, api_key: str, locale: str,
) -> Tuple[str, str]:
    """노드 컨텍스트 추출 → rules/dbms/node_context.yaml"""
    result = prompts.execute_raw(
        "dbms", "node_context",
        {"skeleton_code": skeleton_code, "ancestor_context": ancestor_context,
         "sibling_code": sibling_code, "locale": locale},
        api_key,
    )
    if not isinstance(result, dict):
        raise ValueError(f"node_context 규칙이 dict가 아닌 값을 반환: {type(result)}")
    parent_ctx = result.get("parent_context", "").strip()
    sibling_ctx = result.get("sibling_context", "").strip()
    if not parent_ctx:
        raise ValueError("node_context 규칙이 빈 parent_context를 반환")
    return parent_ctx, sibling_ctx


def analyze_variable_summary(variables: List[Dict[str, Any]], api_key: str, locale: str) -> Dict[str, Any]:
    """변수 역할 요약 → rules/dbms/variable_summary.yaml"""
    return prompts.execute_raw("dbms", "variable_summary", {"variables": variables, "locale": locale}, api_key)


__all__ = [
    "analyze_code", "analyze_dml_tables", "analyze_summary_only",
    "summarize_table_metadata", "analyze_variables", "extract_node_context",
    "analyze_variable_summary",
]
