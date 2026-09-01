"""yaml 기반 LLM 호출 헬퍼 — 단순 LLM 호출 (Agent 외부).

용도: ``pipeline/meaning/<family>/prompts/<strategy>/<name>.yaml`` 의 룰 로딩 + LLM 호출 + Pydantic 응답.
사용처:
- ``pipeline/meaning`` (code_summary /
  code_table_usage_meanings)
- ``pipeline/stages/output`` (module_summary / table_description)

yaml 형식 (단순)::

    system: |
      <system prompt — byte-identical 보존>
    user_template: |
      <user 메시지 template (str.format 치환)>
    model: <옵션 — None 이면 현재 llm_config 의 model 사용>

yaml 형식 (DRY — 분할 그룹의 KV 캐시 prefix 통일)::

    # `pipeline/meaning/<family>/prompts/<strategy>/_common.yaml` — 단일 진실
    system: |
      <분할 공통 system 본문>
    user_template_inputs: |
      <분할 공통 user_template 입력값 영역>

    # `pipeline/meaning/<family>/prompts/<strategy>/<split_name>.yaml` — 분할 고유만
    extends: _common
    user_template_specific: |
      <이 분할 고유 user_template 지침>

``extends`` 가 있으면 ``_common`` 의 ``system`` 을 그대로 두고, user 메시지는
``user_template_specific``(rule 고정) → ``user_template_inputs``(공통 대상별 변동) →
``user_template_dynamic_inputs``(선택적인 rule 전용 고변동 입력) 순으로 조립한다. 변화율이 낮은
내용부터 두어 같은 rule의 여러 대상 호출이 긴 prefix를 재사용한다. 분할 그룹의 system
본문은 글자 단위 동일이다. 자세한 원칙: [[kv-cache]].

실제 provider JSON Schema 호출·usage·내부 Pydantic 검증은
``yaml_llm_runner.run_yaml_rule``이 소유한다. 이 모듈은 실패 시 partial raw와 prompt
evidence를 보존하는 공용 경계만 소유한다.

**Plan §4 M0 핵심**: Agent 외부 호출도 ``usage.jsonl`` 적재 (측정 사각지대 차단).
``CostTrackingMiddleware`` 와 같은 헬퍼 ``llm.usage.usage_jsonl_writer.persist_usage_and_charge_budget``
사용.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional, TypeVar

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from shared.config.app_settings import SETTINGS
from llm.usage.cost_budget import CostBudget
from llm.llm_client import build_llm_client
from llm.configs.config_loader import load_llm_config
from llm.usage.model_pricing import compute_cost
from llm.usage.token_usage import UnmeteredLlmCallError, track_usage
from llm.usage.usage_jsonl_writer import persist_usage_and_charge_budget
from shared.events.runtime_logger import log_info, log_warning
from llm.prompt_filters import apply_cache_friendly_filters
from llm.request_tracing import write_rule_record


T = TypeVar("T", bound=BaseModel)


from llm.runners.prompt_values import _extract_target_and_sort

def _write_error_record(
    rule_name: str,
    system_prompt: str,
    user_content: str,
    user_values: dict,
    error: BaseException,
    elapsed_ms: int,
    response_schema: dict | None = None,
    structured_output_method: str | None = None,
    streamed_partial: str = "",
) -> None:
    """LLM 호출 실패 시에도 prompt + error 적재 — 사후 디버깅 (silent skip 차단).

    LengthFinishReasonError / 401 / 429 / timeout 같은 폭주 / 예외 케이스에서
    prompt 가 사라지면 원인 분석 불가. except 가 raise 하기 전 호출 — 적재 자체
    실패는 graceful (분석 흐름은 이미 raise 직전이라 막힐 거 없음).

    ★ ``LengthFinishReasonError`` 는 ``completion`` 속성에 partial response 보유 —
    폭주 내용 (반복 패턴 / 쓸데없는 내용) 진단을 위해 추출 적재.
    """
    partial_content = _extract_partial_content(error) or streamed_partial
    try:
        target_id, sort_key = _extract_target_and_sort(user_values)
        write_rule_record(
            rule_name=rule_name,
            target_id=target_id,
            system_prompt=system_prompt,
            user_content=user_content,
            response=partial_content if partial_content else None,
            metadata={
                "error": f"{type(error).__name__}: {error}",
                "latency_ms": elapsed_ms,
                "partial_chars": len(partial_content) if partial_content else 0,
                "structured_output_method": structured_output_method,
            },
            sort_key=sort_key,
            response_schema=response_schema,
        )
    except Exception as e:  # noqa: BLE001 — graceful (이미 raise 직전)
        log_warning(
            "llm.runners.yaml_llm_runner",
            "write_error_record",
            "에러 발생 prompt 적재 실패 — 디버깅 보조만 누락 (raise 는 계속)",
            rule_name=rule_name,
            inner_error=f"{type(e).__name__}: {e}",
        )


def _extract_partial_content(error: BaseException) -> str:
    """예외가 실어 온 partial response 를 추출한다 — 없으면 빈 문자열 (graceful).

    지원 경로 (spec 116 0단계 — 실패 시에도 raw 를 지우지 않는다):
    - ``partial_text`` 속성: ``RepetitionLoopAborted`` 가 중단 시점까지의 수신 tail 을 실음.
    - pydantic ``ValidationError``: 직접 adapter나 과거 evidence에서 예외의
      ``errors()[*]['input']``에 실린 파싱 입력을 복원한다. 현행 yaml runner는 provider
      JSON Schema 결과의 raw를 먼저 확보한 뒤 자체 Pydantic 검증 실패를 정상 record에
      남기므로 이 예외 복원에 의존하지 않는다.
    - ``completion`` 속성: OpenAI SDK ``LengthFinishReasonError`` 가
      ``completion.choices[0].message.content`` 에 절단 본문을 실음.

    폭주 진단용 — 반복 패턴 / 쓸데없는 내용 / 무한 list 등을 사람·LLM 이 확인.
    """
    partial_text = getattr(error, "partial_text", "")
    if isinstance(partial_text, str) and partial_text:
        return partial_text
    errors_fn = getattr(error, "errors", None)
    if callable(errors_fn):
        try:
            import json as _json

            inputs = []
            for item in errors_fn():
                value = item.get("input")
                if value is None:
                    continue
                inputs.append(
                    value if isinstance(value, str)
                    else _json.dumps(value, ensure_ascii=False, default=str)
                )
            if inputs:
                # 같은 입력이 필드별로 반복 보고될 수 있어 첫 원문만 보존한다.
                return inputs[0]
        except Exception as extraction_error:  # noqa: BLE001 — 원 예외는 그대로 보존
            log_warning(
                "llm.runners.model_invocation",
                "extract_validation_input_failed",
                "검증 오류의 partial 입력 복원 실패 — 원 호출 오류는 그대로 전파",
                error=f"{type(extraction_error).__name__}: {extraction_error}",
            )
    completion = getattr(error, "completion", None)
    if completion is None:
        return ""
    try:
        choices = getattr(completion, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", "") or ""
        return content
    except (IndexError, TypeError, AttributeError):
        # completion 객체 모양이 예상과 다름 — partial 추출만 포기(원 error 는 호출부가 그대로 raise).
        return ""
