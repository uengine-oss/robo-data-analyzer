"""RuleLoader 기반 LLM 호출 헬퍼 통합

_ast_core.py에서 추출한 LLM 호출 함수들.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from util.prompt_executor import RuleLoader


# ==================== RuleLoader 헬퍼 ====================
_rule_loader_cache: Optional[RuleLoader] = None

def _rule_loader() -> RuleLoader:
    """RuleLoader 인스턴스를 캐싱하여 재사용"""
    global _rule_loader_cache
    if _rule_loader_cache is None:
        _rule_loader_cache = RuleLoader(target_lang="dbms")
    return _rule_loader_cache


def analyze_code(code: str, context: str, ranges: list, count: int, api_key: str, locale: str) -> Dict[str, Any]:
    """코드 분석"""
    inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return _rule_loader().execute("node_analysis", inputs, api_key)


def analyze_dml_tables(code: str, context: str, ranges: list, api_key: str, locale: str) -> Dict[str, Any]:
    """DML 테이블 분석"""
    inputs = {"code": code, "ranges": ranges, "locale": locale}
    if context.strip():
        inputs["context"] = context
    return _rule_loader().execute("dml_extract", inputs, api_key)


def analyze_summary_only(summaries: dict, api_key: str, locale: str, previous_summary: str = "") -> Dict[str, Any]:
    """프로시저/함수 전체 요약 생성 (Summary만)."""
    return _rule_loader().execute(
        "procedure_summary",
        {"summaries": summaries, "locale": locale, "previous_summary": previous_summary},
        api_key,
    )



def summarize_table_metadata(
    table_name: str,
    table_sentences: list,
    column_sentences: dict,
    column_metadata: dict,
    api_key: str,
    locale: str,
) -> Dict[str, Any]:
    return _rule_loader().execute(
        "table_summary",
        {
            "table_name": table_name,
            "table_sentences": table_sentences,
            "column_sentences": column_sentences,
            "column_metadata": column_metadata,
            "locale": locale,
        },
        api_key,
    )


def analyze_variables(declaration_code: str, api_key: str, locale: str) -> Dict[str, Any]:
    return _rule_loader().execute(
        "variable_analysis",
        {"declaration_code": declaration_code, "locale": locale},
        api_key,
    )


def extract_node_context(
    skeleton_code: str,
    ancestor_context: str,
    sibling_code: str,
    api_key: str,
    locale: str,
) -> Tuple[str, str]:
    """부모 스켈레톤 + 이전 형제 코드에서 컨텍스트 추출"""
    result = _rule_loader().execute(
        "node_context",
        {
            "skeleton_code": skeleton_code,
            "ancestor_context": ancestor_context,
            "sibling_code": sibling_code,
            "locale": locale,
        },
        api_key,
    )

    if not isinstance(result, dict):
        raise ValueError(f"node_context 규칙이 dict가 아닌 값을 반환했습니다: {type(result)}")

    parent_ctx = result.get("parent_context", "").strip()
    sibling_ctx = result.get("sibling_context", "").strip()

    if not parent_ctx:
        raise ValueError("node_context 규칙이 빈 parent_context를 반환했습니다")

    return parent_ctx, sibling_ctx


def analyze_variable_summary(variables: List[Dict[str, Any]], api_key: str, locale: str) -> Dict[str, Any]:
    """변수 역할 목록을 하나의 요약 설명으로 통합합니다."""
    return _rule_loader().execute(
        "variable_summary",
        {"variables": variables, "locale": locale},
        api_key,
    )


__all__ = [
    "_rule_loader",
    "analyze_code",
    "analyze_dml_tables",
    "analyze_summary_only",
    "summarize_table_metadata",
    "analyze_variables",
    "extract_node_context",
    "analyze_variable_summary",
]
