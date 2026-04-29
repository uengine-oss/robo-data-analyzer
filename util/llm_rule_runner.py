"""LLM 프롬프트 로더, 실행기, 레지스트리

Section 1: YAML 기반 프롬프트 로더 및 실행기 (구 prompt_executor.py)
Section 2: 프롬프트 레지스트리 — 모든 LLM 호출의 단일 진입점 (구 prompt_registry.py)

사용 (모든 호출은 sync):
    from util.llm_rule_runner import prompts
    result = prompts.call_extract(items, language, api_key)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# Section 1: Rule Loader + Executor
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import Any, Dict, List

import yaml
from jinja2 import Template, TemplateError

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda

from util.llm_audit_logger import invoke_with_audit
from client.llm_client import get_llm
from config.settings import settings

# 공용 가드레일 (sanitize · retry 분류 · 피드백 빌더) — 모든 LLM 호출 경로에서 동일 동작 보장
from util.llm_call_safety import (
    sanitize_llm_output as _sanitize_llm_output,
    build_retry_feedback as _build_retry_feedback,
    is_parse_failure,
    is_rate_limit,
    is_bad_request_parse,
)

# 재시도/증액 설정은 settings.llm 에서 읽음 (env 로 조정 가능)
#   LLM_MAX_RETRIES           — rate/400 계열 기본 상한 (default 3)
#   LLM_MAX_PARSE_RETRIES     — parse 실패 상한 (default 5)
#   LLM_RETRY_BASE_DELAY      — exponential backoff 기본값
#   LLM_PARSE_RETRY_TOKEN_BUMP — parse 실패 시 max_tokens 증액 배수 (default 1.5)
#   LLM_MAX_TOKEN_CEILING     — 모델 context 상한 (default 32768)

# OutputParserException 메시지에서 실패한 응답 본문만 추출
_INVALID_JSON_TAIL_RE = re.compile(
    r'Invalid json output:\s*(.*?)(?:For troubleshooting|$)',
    re.DOTALL,
)


logger = logging.getLogger(__name__)


def _extract_response_tail(error: Exception, limit: int = 600) -> str:
    """파싱 실패 예외에서 실제 응답 본문(꼬리)만 추출."""
    m = _INVALID_JSON_TAIL_RE.search(str(error))
    if not m:
        return ""
    body = m.group(1).strip()
    return body[-limit:] if len(body) > limit else body


def _safe_copy(value: Any) -> Any:
    """JSON 직렬화 가능한 형태로 깊은 복사"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return value


def _log_finish_reason(rule_name: str):
    """LLM 응답의 finish_reason 을 로깅하는 RunnableLambda 팩토리.

    `length` 면 max_tokens 잘림 (JSON invalid 주원인). `content_filter`, `tool_calls` 등도 기록.
    정상 `stop` 은 로그하지 않음.
    """
    def _inner(msg):
        # finish_reason 추출은 메타데이터 부재/형식 다양성을 허용 — 로깅 보조 용도라 진행 자체를 막지 않음
        reason = getattr(msg, "response_metadata", None) or {}
        if isinstance(reason, dict):
            fr = reason.get("finish_reason")
            if fr and fr != "stop":
                logger.warning(
                    "[RULE:FINISH] rule=%s finish_reason=%s (%s)",
                    rule_name, fr,
                    "max_tokens 초과로 응답 잘림 — JSON invalid 원인 가능" if fr == "length"
                    else "비정상 종료 — JSON parse 실패 가능",
                )
        return msg
    return _inner


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
        current_max_tokens = int(getattr(llm, "max_tokens", None) or 0)

        def _build_chain(llm_inst):
            return (
                RunnablePassthrough()
                | PromptTemplate.from_template("{prompt}")
                | llm_inst
                | RunnableLambda(_log_finish_reason(rule_name))
                | RunnableLambda(_sanitize_llm_output)
                | JsonOutputParser()
            )

        cfg = settings.llm
        chain = _build_chain(llm)
        last_error: Exception | None = None
        response_tail: str = ""
        # parse 실패는 max_parse_retries, rate/400 은 max_retries. 둘 중 큰 값으로 루프 돌고 내부 조건으로 분기.
        loop_max = max(cfg.max_retries, cfg.max_parse_retries)
        for attempt in range(loop_max + 1):
            # 재시도 시 이전 오류 원인 + 실패 응답 꼬리를 프롬프트에 덧붙여 모델에 수정 요청
            current_prompt = prompt_text
            if attempt > 0 and last_error is not None:
                current_prompt = prompt_text + "\n\n" + _build_retry_feedback(
                    last_error, attempt, response_tail=response_tail,
                )
            try:
                result = invoke_with_audit(
                    chain,
                    {"prompt": current_prompt},
                    prompt_name=f"rules/{self.target_lang}/{rule_name}",
                    input_payload={
                        "inputs": _safe_copy(inputs),
                        "renderedPrompt": current_prompt,
                    },
                    metadata={
                        "rule": rule_name,
                        "targetLang": self.target_lang,
                        "rulePath": rule_path,
                        "retryAttempt": attempt,
                    },
                )
                return result

            except Exception as e:
                last_error = e
                _is_rate = is_rate_limit(e)
                _is_bad = is_bad_request_parse(e)
                _is_parse = is_parse_failure(e)

                # 오류 유형별로 허용 재시도 횟수 분기
                effective_max = cfg.max_parse_retries if _is_parse else cfg.max_retries

                if (_is_rate or _is_bad or _is_parse) and attempt < effective_max:
                    delay = cfg.retry_base_delay * (2 ** attempt) if (_is_rate or _is_bad) else 0.5
                    reason = "parse 실패" if _is_parse else ("rate" if _is_rate else "400")

                    if _is_parse:
                        # parse 실패 → 실패 응답 꼬리 저장 + max_tokens 증액
                        response_tail = _extract_response_tail(e)
                        if current_max_tokens:
                            new_max = min(int(current_max_tokens * cfg.parse_retry_token_bump), cfg.max_token_ceiling)
                            if new_max > current_max_tokens:
                                logger.info(
                                    "[RULE] max_tokens 증액 | rule=%s | %d → %d (parse 실패 대응)",
                                    rule_name, current_max_tokens, new_max,
                                )
                                current_max_tokens = new_max
                                chain = _build_chain(llm.bind(max_tokens=new_max))

                    logger.warning(
                        "[RULE] 재시도 | rule=%s | attempt=%d/%d | 원인=%s | %.1fs 후",
                        rule_name, attempt + 1, effective_max, reason, delay,
                    )
                    time.sleep(delay)
                    continue

                logger.error("[RULE] 프롬프트 실행 실패 | rule=%s | error=%s", rule_name, e, exc_info=True)
                raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {e}")

        raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {last_error}")

# ---------------------------------------------------------------------------
# 싱글톤 팩토리
# ---------------------------------------------------------------------------

_loaders: Dict[str, "RuleLoader"] = {}


def get_rule_loader(target_lang: str) -> "RuleLoader":
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
    def call_extract(
        items: List[Dict], language: str, api_key: str,
        codebase_pattern: str = "",
    ) -> Dict[str, Any]:
        """함수 호출 관계 추출. -> rules/framework/call_extract.yaml

        codebase_pattern: Profile Agent 가 정리한 반복 패턴 + "심볼 역할 분류" 표.
        LLM 에 래퍼 매크로 언래핑·로깅 제외 지침을 전달하는 용도.
        """
        return get_rule_loader("framework").execute(
            "call_extract",
            {"items": items, "language": language, "codebase_pattern": codebase_pattern},
            api_key=api_key,
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
    def execute_raw(target_lang: str, rule_name: str, inputs: dict, api_key: str, model: str | None = None) -> dict:
        """inputs dict를 직접 전달하여 동기 실행."""
        return get_rule_loader(target_lang).execute(rule_name, inputs, api_key=api_key, model=model)


# 전역 싱글톤
prompts = PromptRegistry()
