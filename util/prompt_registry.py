"""프롬프트 레지스트리 — 모든 LLM 호출의 단일 진입점.

모든 프롬프트 호출이 이 파일에 등록되어 있어, 어디서 어떤 프롬프트가 어떤 입력/출력으로
호출되는지 한 곳에서 파악 가능.

사용:
    from util.prompt_registry import prompts
    result = await prompts.call_extract(items, language, api_key)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from util.prompt_executor import get_rule_loader

logger = logging.getLogger(__name__)


class PromptRegistry:
    """모든 LLM 프롬프트 호출의 중앙 레지스트리.

    각 메서드는 하나의 YAML rule에 대응. 입력/출력 타입이 명시적.
    내부에서 get_rule_loader().execute()를 호출.
    """

    # ═══════════════════════════════════════════════════════════════
    # Framework Rules
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def call_extract(items: List[Dict], language: str, api_key: str) -> Dict[str, Any]:
        """함수 호출 관계 추출. → rules/framework/call_extract.yaml"""
        return get_rule_loader("framework").execute(
            "call_extract", {"items": items, "language": language}, api_key=api_key,
        )

    @staticmethod
    def code_analysis(
        code: str, ranges: List[Dict], count: int, locale: str,
        api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """코드 분석 (summary + stereotype + dependency + calls + variables + tableInsights).
        → rules/framework/code_analysis.yaml (기존 function_summary.yaml)"""
        inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale, **kwargs}
        return get_rule_loader("framework").execute("code_analysis", inputs, api_key=api_key)

    @staticmethod
    def table_extract(sql_items: List[Dict], api_key: str) -> Dict[str, Any]:
        """SQL → 테이블/컬럼/FK 추출. → rules/framework/table_extract.yaml"""
        return get_rule_loader("framework").execute(
            "table_extract", {"sql_items": sql_items}, api_key=api_key,
        )

    @staticmethod
    def module_analysis(
        module_fqn: str, items: List[Dict], locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """모듈 분석 (summary + stereotype + unified_actors).
        → rules/framework/module_analysis.yaml (기존 module_summary.yaml)"""
        inputs = {"module_fqn": module_fqn, "items": items, "locale": locale, **kwargs}
        return get_rule_loader("framework").execute("module_analysis", inputs, api_key=api_key)

    @staticmethod
    def variable_summary(code: str, locale: str, api_key: str, **kwargs) -> Dict[str, Any]:
        """변수 요약. → rules/framework/variable_summary.yaml"""
        inputs = {"code": code, "locale": locale, **kwargs}
        return get_rule_loader("framework").execute("variable_summary", inputs, api_key=api_key)

    @staticmethod
    def actor_synonym_merge(
        actor_module_list: str, locale: str, api_key: str,
    ) -> Dict[str, Any]:
        """Actor 동의어 병합. → rules/framework/actor_synonym_merge.yaml"""
        return get_rule_loader("framework").execute(
            "actor_synonym_merge",
            {"actor_module_list": actor_module_list, "locale": locale},
            api_key=api_key,
        )

    # ═══════════════════════════════════════════════════════════════
    # DBMS Rules
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def dbms_node_analysis(
        code: str, ranges: List[Dict], count: int, locale: str,
        api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """DBMS 코드 분석. → rules/dbms/node_analysis.yaml"""
        inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("node_analysis", inputs, api_key=api_key)

    @staticmethod
    def dbms_dml_extract(
        code: str, ranges: List[Dict], locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """DBMS DML 추출. → rules/dbms/dml_extract.yaml"""
        inputs = {"code": code, "ranges": ranges, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("dml_extract", inputs, api_key=api_key)

    @staticmethod
    def dbms_unit_analysis(
        summaries: str, locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """DBMS 프로시저/함수 분석. → rules/dbms/procedure_summary.yaml"""
        inputs = {"summaries": summaries, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("unit_analysis", inputs, api_key=api_key)

    @staticmethod
    def table_summary(
        table_name: str, table_sentences: List[str],
        column_sentences: Dict, column_metadata: Dict,
        locale: str, api_key: str,
    ) -> Dict[str, Any]:
        """테이블 설명 보강. → rules/dbms/table_summary.yaml"""
        return get_rule_loader("dbms").execute(
            "table_summary",
            {
                "table_name": table_name,
                "table_sentences": table_sentences,
                "column_sentences": column_sentences,
                "column_metadata": column_metadata,
                "locale": locale,
            },
            api_key=api_key,
        )

    @staticmethod
    def dbms_variable_analysis(declaration_code: str, locale: str, api_key: str) -> Dict[str, Any]:
        """DBMS 변수 분석. → rules/dbms/variable_analysis.yaml"""
        return get_rule_loader("dbms").execute(
            "variable_analysis",
            {"declaration_code": declaration_code, "locale": locale},
            api_key=api_key,
        )

    @staticmethod
    def dbms_variable_summary(variables: str, locale: str, api_key: str) -> Dict[str, Any]:
        """DBMS 변수 요약. → rules/dbms/variable_summary.yaml"""
        return get_rule_loader("dbms").execute(
            "variable_summary",
            {"variables": variables, "locale": locale},
            api_key=api_key,
        )

    @staticmethod
    def dbms_node_context(
        skeleton_code: str, locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """DBMS 노드 컨텍스트 추출. → rules/dbms/node_context.yaml"""
        inputs = {"skeleton_code": skeleton_code, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("node_context", inputs, api_key=api_key)

    # ═══════════════════════════════════════════════════════════════
    # DeepAgent Rules (render만 — execute는 agent가 직접 수행)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def deep_agent_system_prompt(**kwargs) -> str:
        """DeepAgent 시스템 프롬프트. → rules/deep_agent/system.yaml"""
        return get_rule_loader("deep_agent").render_prompt("system", kwargs)

    @staticmethod
    def deep_agent_entity_prompt(**kwargs) -> str:
        """DeepAgent 엔티티 식별 프롬프트. → rules/deep_agent/entity.yaml"""
        return get_rule_loader("deep_agent").render_prompt("entity", kwargs)

    @staticmethod
    def deep_agent_varref_prompt(**kwargs) -> str:
        """DeepAgent 변수 참조 프롬프트. → rules/deep_agent/variable_reference.yaml"""
        return get_rule_loader("deep_agent").render_prompt("variable_reference", kwargs)


    # ═══════════════════════════════════════════════════════════════
    # Raw execute (inputs dict를 직접 전달)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def execute_raw(target_lang: str, rule_name: str, inputs: dict, api_key: str, model: str = None) -> dict:
        """inputs dict를 직접 전달하여 동기 실행."""
        return get_rule_loader(target_lang).execute(rule_name, inputs, api_key=api_key, model=model)

    @staticmethod
    async def async_execute_raw(target_lang: str, rule_name: str, inputs: dict, api_key: str, model: str = None) -> dict:
        """inputs dict를 직접 전달하여 비동기 실행 (LLMRateLimiter + ainvoke)."""
        return await get_rule_loader(target_lang).async_execute(rule_name, inputs, api_key=api_key, model=model)


# 전역 싱글톤
prompts = PromptRegistry()
