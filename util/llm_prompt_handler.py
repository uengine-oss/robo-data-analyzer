"""LLM 프롬프트 로더, 실행기, 레지스트리

Section 1: YAML 기반 프롬프트 로더 및 실행기 (구 prompt_executor.py)
Section 2: 프롬프트 레지스트리 — 모든 LLM 호출의 단일 진입점 (구 prompt_registry.py)

사용:
    from util.llm_prompt_handler import prompts
    result = await prompts.call_extract(items, language, api_key)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# Section 1: Rule Loader + Executor
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Template, TemplateError
import re as _re

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda

from util.llm_audit_logger import invoke_with_audit
from client.llm_client import get_llm

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds

# ---------------------------------------------------------------------------
# LLM 출력 정제 (JSON 파싱 전처리)
# ---------------------------------------------------------------------------

# JSON 파싱을 깨뜨리는 제어 문자 (null byte, SOH, STX 등) 매칭 패턴.
# 줄바꿈(\n=0x0a), 캐리지리턴(\r=0x0d), 탭(\t=0x09)은 유효하므로 제외.
_CONTROL_CHAR_RE = _re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

# JSON 표준 이스케이프 시퀀스에서 백슬래시 뒤에 올 수 있는 유효 문자
_VALID_JSON_ESCAPE_CHARS = frozenset({'"', '\\', '/', 'b', 'f', 'n', 'r', 't'})


def _fix_invalid_json_escapes(s: str) -> str:
    r"""JSON 문자열 내부의 잘못된 이스케이프 시퀀스를 수리한다.

    유효한 이스케이프(\" \\ \/ \b \f \n \r \t \uXXXX)는 그대로 보존하고,
    그 외(\0, \a, \' 등)는 백슬래시만 제거하여 일반 문자로 변환한다.

    예: ``"hello\\world"`` -> ``"helloworld"`` (\\w 는 유효하지 않으므로 \\ 제거)
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            next_ch = s[i + 1]
            if next_ch in _VALID_JSON_ESCAPE_CHARS:
                # 유효한 이스케이프 쌍 -> 그대로 유지
                result.append(s[i])
                result.append(next_ch)
                i += 2
            elif next_ch == 'u' and i + 5 < len(s):
                # \uXXXX 유니코드 이스케이프 -> 그대로 유지
                result.append(s[i:i + 6])
                i += 6
            else:
                # 잘못된 이스케이프 -> 백슬래시 제거, 다음 문자만 유지
                result.append(next_ch)
                i += 2
        else:
            result.append(s[i])
            i += 1
    return ''.join(result)


def _strip_code_fences(s: str) -> str:
    """코드펜스(```json ... ```) 래핑과 JSON 값 내부 백틱을 제거한다."""
    stripped = s.strip()
    # 코드펜스 래핑 제거: ```json\n...\n``` or ```\n...\n```
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    # JSON 값 내부 백틱 제거 (JSON 키/구조에는 백틱이 없음)
    stripped = stripped.replace("`", "")
    return stripped


def _sanitize_llm_output(text) -> str:
    """LLM 응답에서 제어 문자, 코드펜스, 백틱, 잘못된 JSON 이스케이프를 수리하여 파싱 실패를 방지한다.

    AIMessage(content=...) 객체와 순수 str 모두 처리한다.
    Responses API는 content를 list[dict]로 반환할 수 있으므로 이를 string으로 변환한다.
    """
    if hasattr(text, 'content'):
        # Responses API: content가 list인 경우 text 블록들을 합침
        if isinstance(text.content, list):
            text.content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in text.content
            )
        text.content = _CONTROL_CHAR_RE.sub('', text.content)
        text.content = _strip_code_fences(text.content)
        text.content = _fix_invalid_json_escapes(text.content)
        return text
    if isinstance(text, str):
        text = _CONTROL_CHAR_RE.sub('', text)
        text = _strip_code_fences(text)
        return _fix_invalid_json_escapes(text)
    return text

logger = logging.getLogger(__name__)


def _safe_copy(value: Any) -> Any:
    """JSON 직렬화 가능한 형태로 깊은 복사"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return value


class RuleLoader:
    """YAML 기반 프롬프트/템플릿 로더

    Args:
        target_lang: 타겟 언어 (dbms, framework)
    """

    __slots__ = ("target_lang", "rule_dir")

    def __init__(self, target_lang: str = "framework"):
        self.target_lang = target_lang

        base_dir = os.path.dirname(os.path.dirname(__file__))
        self.rule_dir = os.path.join(base_dir, "rules", target_lang)

        if not os.path.exists(self.rule_dir):
            raise FileNotFoundError(f"Rule 디렉토리가 존재하지 않습니다: {self.rule_dir}")

    @lru_cache(maxsize=32)
    def _load_rule(self, rule_name: str) -> Dict[str, Any]:
        """Rule YAML 파일 로드 (캐싱)"""
        rule_path = os.path.join(self.rule_dir, f"{rule_name}.yaml")

        if not os.path.exists(rule_path):
            raise FileNotFoundError(f"Rule 파일이 존재하지 않습니다: {rule_path}")

        try:
            with open(rule_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML 파싱 오류 ({rule_path}): {e}")

    def validate_inputs(self, rule: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
        """입력값 검증 및 기본값 설정"""
        validated = inputs.copy()

        required = rule.get("input_schema", {}).get("required", [])
        missing = [f for f in required if f not in validated]
        if missing:
            raise ValueError(f"필수 필드 누락: {', '.join(missing)}")

        optional = rule.get("input_schema", {}).get("optional", {})
        if isinstance(optional, dict):
            for field, spec in optional.items():
                if field not in validated and isinstance(spec, dict) and "default" in spec:
                    validated[field] = spec["default"]

        return validated

    def render_prompt(self, rule_name: str, inputs: Dict[str, Any]) -> str:
        """프롬프트 렌더링 (LLM 미호출)"""
        rule = self._load_rule(rule_name)
        validated = self.validate_inputs(rule, inputs)

        try:
            return Template(rule["prompt"]).render(**validated)
        except TemplateError as e:
            raise ValueError(f"프롬프트 렌더링 오류 ({rule_name}): {e}")

    def execute(
        self,
        rule_name: str,
        inputs: Dict[str, Any],
        api_key: str,
        model: str | None = None,
    ) -> Dict[str, Any]:
        """프롬프트 실행 (LLM 호출, 429 Rate Limit 자동 재시도)"""
        _ = self._load_rule(rule_name)
        prompt_text = self.render_prompt(rule_name, inputs)
        prompt_text = prompt_text.replace("\x00", "").replace("\ufeff", "")
        rule_path = os.path.join(self.rule_dir, f"{rule_name}.yaml")

        llm = get_llm(api_key=api_key, model=model)
        chain = (
            RunnablePassthrough()
            | PromptTemplate.from_template("{prompt}")
            | llm
            | RunnableLambda(_sanitize_llm_output)
            | JsonOutputParser()
        )

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = invoke_with_audit(
                    chain,
                    {"prompt": prompt_text},
                    prompt_name=f"rules/{self.target_lang}/{rule_name}",
                    input_payload={
                        "inputs": _safe_copy(inputs),
                        "renderedPrompt": prompt_text,
                    },
                    metadata={
                        "rule": rule_name,
                        "targetLang": self.target_lang,
                        "rulePath": rule_path,
                    },
                )
                return result

            except Exception as e:
                last_error = e
                err_str = str(e)
                is_rate_limit = "429" in err_str or "rate" in err_str.lower() or "quota" in err_str.lower()
                is_bad_request = "400" in err_str and "parse" in err_str.lower()

                if (is_rate_limit or is_bad_request) and attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.info(
                        "[RULE] Rate Limit 재시도 | rule=%s | attempt=%d/%d | %.1fs 후",
                        rule_name, attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue

                logger.error("[RULE] 프롬프트 실행 실패 | rule=%s | error=%s", rule_name, e)
                raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {e}")

        raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {last_error}")

    async def async_execute(
        self,
        rule_name: str,
        inputs: Dict[str, Any],
        api_key: str,
        model: str | None = None,
    ) -> Dict[str, Any]:
        """프롬프트 비동기 실행 (LLMRateLimiter + asyncio.sleep 재시도)."""
        import asyncio
        from util.llm_rate_limiter import get_rate_limiter

        _ = self._load_rule(rule_name)
        prompt_text = self.render_prompt(rule_name, inputs)
        prompt_text = prompt_text.replace("\x00", "").replace("\ufeff", "")
        rule_path = os.path.join(self.rule_dir, f"{rule_name}.yaml")

        llm = get_llm(api_key=api_key, model=model)
        chain = (
            RunnablePassthrough()
            | PromptTemplate.from_template("{prompt}")
            | llm
            | RunnableLambda(_sanitize_llm_output)
            | JsonOutputParser()
        )

        from util.llm_audit_logger import ainvoke_with_audit

        limiter = get_rate_limiter()
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with limiter:
                    result = await ainvoke_with_audit(
                        chain,
                        {"prompt": prompt_text},
                        prompt_name=f"rules/{self.target_lang}/{rule_name}",
                        input_payload={"inputs": _safe_copy(inputs), "renderedPrompt": prompt_text},
                        metadata={"rule": rule_name, "targetLang": self.target_lang, "rulePath": rule_path},
                    )
                return result
            except Exception as e:
                last_error = e
                err_str = str(e)
                is_rate_limit = "429" in err_str or "rate" in err_str.lower() or "quota" in err_str.lower()
                is_bad_request = "400" in err_str and "parse" in err_str.lower()
                if (is_rate_limit or is_bad_request) and attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.info(
                        "[RULE] Rate Limit 재시도 | rule=%s | attempt=%d/%d | %.1fs 후",
                        rule_name, attempt + 1, _MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)  # 논블로킹 대기
                    continue
                logger.error("[RULE] 프롬프트 실행 실패 | rule=%s | error=%s", rule_name, e)
                raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {e}")
        raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {last_error}")

# ---------------------------------------------------------------------------
# 싱글톤 팩토리
# ---------------------------------------------------------------------------

_loaders: Dict[str, "RuleLoader"] = {}


def get_rule_loader(target_lang: str = "exp") -> "RuleLoader":
    """target_lang별 RuleLoader 싱글톤. 매번 생성하지 않고 캐싱."""
    if target_lang not in _loaders:
        _loaders[target_lang] = RuleLoader(target_lang=target_lang)
    return _loaders[target_lang]


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2: Prompt Registry
# ═══════════════════════════════════════════════════════════════════════════════


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
        """함수 호출 관계 추출. -> rules/framework/call_extract.yaml"""
        return get_rule_loader("framework").execute(
            "call_extract", {"items": items, "language": language}, api_key=api_key,
        )

    @staticmethod
    def code_analysis(
        code: str, ranges: List[Dict], count: int, locale: str,
        api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """코드 분석 (summary + stereotype + dependency + calls + variables + tableInsights).
        -> rules/framework/code_analysis.yaml"""
        inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale, **kwargs}
        return get_rule_loader("framework").execute("code_analysis", inputs, api_key=api_key)

    @staticmethod
    def table_extract(sql_items: List[Dict], api_key: str) -> Dict[str, Any]:
        """SQL -> 테이블/컬럼/FK 추출. -> rules/framework/table_extract.yaml"""
        return get_rule_loader("framework").execute(
            "table_extract", {"sql_items": sql_items}, api_key=api_key,
        )

    @staticmethod
    def module_analysis(
        module_fqn: str, items: List[Dict], locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """모듈 분석 (summary + stereotype + unified_actors).
        -> rules/framework/module_analysis.yaml (기존 module_summary.yaml)"""
        inputs = {"module_fqn": module_fqn, "items": items, "locale": locale, **kwargs}
        return get_rule_loader("framework").execute("module_analysis", inputs, api_key=api_key)

    @staticmethod
    def variable_summary(code: str, locale: str, api_key: str, **kwargs) -> Dict[str, Any]:
        """변수 요약. -> rules/framework/variable_summary.yaml"""
        inputs = {"code": code, "locale": locale, **kwargs}
        return get_rule_loader("framework").execute("variable_summary", inputs, api_key=api_key)

    @staticmethod
    def actor_synonym_merge(
        actor_module_list: str, locale: str, api_key: str,
    ) -> Dict[str, Any]:
        """Actor 동의어 병합. -> rules/framework/actor_synonym_merge.yaml"""
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
        """DBMS 코드 분석. -> rules/dbms/node_analysis.yaml"""
        inputs = {"code": code, "ranges": ranges, "count": count, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("node_analysis", inputs, api_key=api_key)

    @staticmethod
    def dbms_dml_extract(
        code: str, ranges: List[Dict], locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """DBMS DML 추출. -> rules/dbms/dml_extract.yaml"""
        inputs = {"code": code, "ranges": ranges, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("dml_extract", inputs, api_key=api_key)

    @staticmethod
    def dbms_unit_analysis(
        summaries: str, locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """DBMS 프로시저/함수 분석. -> rules/dbms/procedure_summary.yaml"""
        inputs = {"summaries": summaries, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("unit_analysis", inputs, api_key=api_key)

    @staticmethod
    def table_summary(
        table_name: str, table_sentences: List[str],
        column_sentences: Dict, column_metadata: Dict,
        locale: str, api_key: str,
    ) -> Dict[str, Any]:
        """테이블 설명 보강. -> rules/dbms/table_summary.yaml"""
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
        """DBMS 변수 분석. -> rules/dbms/variable_analysis.yaml"""
        return get_rule_loader("dbms").execute(
            "variable_analysis",
            {"declaration_code": declaration_code, "locale": locale},
            api_key=api_key,
        )

    @staticmethod
    def dbms_variable_summary(variables: str, locale: str, api_key: str) -> Dict[str, Any]:
        """DBMS 변수 요약. -> rules/dbms/variable_summary.yaml"""
        return get_rule_loader("dbms").execute(
            "variable_summary",
            {"variables": variables, "locale": locale},
            api_key=api_key,
        )

    @staticmethod
    def dbms_node_context(
        skeleton_code: str, locale: str, api_key: str, **kwargs,
    ) -> Dict[str, Any]:
        """DBMS 노드 컨텍스트 추출. -> rules/dbms/node_context.yaml"""
        inputs = {"skeleton_code": skeleton_code, "locale": locale, **kwargs}
        return get_rule_loader("dbms").execute("node_context", inputs, api_key=api_key)

    # ═══════════════════════════════════════════════════════════════
    # Raw execute (inputs dict를 직접 전달)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def execute_raw(target_lang: str, rule_name: str, inputs: dict, api_key: str, model: str = None) -> dict:
        """inputs dict를 직접 전달하여 동기 실행."""
        return get_rule_loader(target_lang).execute(rule_name, inputs, api_key=api_key, model=model)


# 전역 싱글톤
prompts = PromptRegistry()
