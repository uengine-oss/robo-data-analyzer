"""YAML 기반 프롬프트 로더 및 실행기

LLM 프롬프트를 YAML 파일로 관리하고 Jinja2로 렌더링합니다.
기존 rule_loader.py를 리네이밍.

사용법:
    loader = RuleLoader(target_lang="dbms")
    result = loader.execute("analysis", {"code": "..."}, api_key="...")

디렉토리 구조:
    rules/
    ├── dbms/
    │   ├── analysis.yaml
    │   └── ddl.yaml
    └── framework/
        ├── analysis.yaml
        └── field.yaml
"""

import json
import logging
import os
import time
from functools import lru_cache
from typing import Any, Dict

import yaml
from jinja2 import Template, TemplateError
import re as _re

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda

from util.artifact_logger import invoke_with_audit
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

    예: ``"hello\\world"`` → ``"helloworld"`` (\\w 는 유효하지 않으므로 \\ 제거)
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            next_ch = s[i + 1]
            if next_ch in _VALID_JSON_ESCAPE_CHARS:
                # 유효한 이스케이프 쌍 → 그대로 유지
                result.append(s[i])
                result.append(next_ch)
                i += 2
            elif next_ch == 'u' and i + 5 < len(s):
                # \uXXXX 유니코드 이스케이프 → 그대로 유지
                result.append(s[i:i + 6])
                i += 6
            else:
                # 잘못된 이스케이프 → 백슬래시 제거, 다음 문자만 유지
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

    def render_template(self, rule_name: str, inputs: Dict[str, Any]) -> str:
        """템플릿 렌더링 (LLM 미호출)"""
        rule = self._load_rule(rule_name)
        validated = self.validate_inputs(rule, inputs)

        template_content = rule.get("template", "")
        if not template_content:
            raise ValueError(f"템플릿이 정의되지 않았습니다: {rule_name}")

        try:
            return Template(template_content).render(**validated)
        except TemplateError as e:
            raise ValueError(f"템플릿 렌더링 오류 ({rule_name}): {e}")

    def render_prompt(self, rule_name: str, inputs: Dict[str, Any]) -> str:
        """프롬프트 렌더링 (LLM 미호출)"""
        rule = self._load_rule(rule_name)
        validated = self.validate_inputs(rule, inputs)

        try:
            return Template(rule["prompt"]).render(**validated)
        except TemplateError as e:
            raise ValueError(f"프롬프트 렌더링 오류 ({rule_name}): {e}")

    def render_task(self, rule_name: str, inputs: Dict[str, Any]) -> str:
        """task 필드 렌더링 (user_input으로 사용)"""
        rule = self._load_rule(rule_name)
        validated = self.validate_inputs(rule, inputs)

        task_template = rule.get("task", "")
        if not task_template:
            return "위 입력 데이터를 분석하여 결과를 JSON 형식으로 출력하세요."

        try:
            return Template(task_template).render(**validated)
        except TemplateError as e:
            raise ValueError(f"task 렌더링 오류 ({rule_name}): {e}")

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
        # 제어 문자 제거 (null byte, BOM 등 → API 400 에러 방지)
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
                    logger.warning(
                        "[RULE] 429 Rate Limit | rule=%s | attempt=%d/%d | %.1fs 후 재시도",
                        rule_name, attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue

                logger.error("[RULE] 프롬프트 실행 실패 | rule=%s | error=%s", rule_name, e)
                raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {e}")

        raise RuntimeError(f"{rule_name} 프롬프트 실행 중 오류: {last_error}")

    def clear_cache(self):
        """캐시 초기화"""
        self._load_rule.cache_clear()


# ---------------------------------------------------------------------------
# 싱글톤 팩토리
# ---------------------------------------------------------------------------

_loaders: Dict[str, "RuleLoader"] = {}


def get_rule_loader(target_lang: str = "exp") -> "RuleLoader":
    """target_lang별 RuleLoader 싱글톤. 매번 생성하지 않고 캐싱."""
    if target_lang not in _loaders:
        _loaders[target_lang] = RuleLoader(target_lang=target_lang)
    return _loaders[target_lang]
