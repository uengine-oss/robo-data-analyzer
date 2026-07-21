"""yaml 기반 LLM 호출 헬퍼 — 단순 LLM 호출 (Agent 외부).

용도: ``llm/prompts/<strategy>/<name>.yaml`` 의 룰 로딩 + LLM 호출 + Pydantic 응답.
사용처:
- ``pipeline/steps/step7_analyze_code`` (code_summary / code_table_insights /
  code_rules_examples)
- ``pipeline/steps/step8_post_processing`` (module_summary / table_description)
- ``pipeline/steps/step6_build_context/large_code_one_liner``

yaml 형식 (단순)::

    system: |
      <system prompt — byte-identical 보존>
    user_template: |
      <user 메시지 template (str.format 치환)>
    model: <옵션 — None 이면 현재 llm_config 의 model 사용>

yaml 형식 (DRY — 분할 그룹의 KV 캐시 prefix 통일)::

    # `llm/prompts/<strategy>/_common.yaml` — 단일 진실
    system: |
      <분할 공통 system 본문>
    user_template_inputs: |
      <분할 공통 user_template 입력값 영역>

    # `llm/prompts/<strategy>/<split_name>.yaml` — 분할 고유만
    extends: _common
    user_template_specific: |
      <이 분할 고유 user_template 지침>

``extends`` 가 있으면 ``_common`` 의 ``system`` 을 그대로 + ``user_template_inputs`` 를
prefix 로, ``user_template_specific`` 을 그 뒤에 붙여 최종 prompt 조립. 분할 그룹의
system 본문은 글자 단위 동일이 강제됨 (KV 캐시 prefix 적중 보장). 자세한 원칙: [[kv-cache]].

``llm.with_structured_output(response_model, include_raw=True)`` 사용 →
usage_metadata 동시 추출 + Pydantic 자동 파싱.

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

from shared.config.settings import SETTINGS
from llm.usage.cost_budget import CostBudget
from llm.client import build_llm_client
from llm.configs.loader import load_llm_config
from llm.usage.pricing import compute_cost
from llm.usage.token_usage import UnmeteredLlmCallError, track_usage
from llm.usage.usage_jsonl_writer import persist_usage_and_charge_budget
from shared.events.runtime_logger import log_info, log_warning
from llm.filters import apply_cache_friendly_filters
from llm.tracing import write_rule_record


T = TypeVar("T", bound=BaseModel)


from llm.runners.prompt_values import _extract_target_and_sort

def _write_error_record(
    rule_name: str,
    system_prompt: str,
    user_content: str,
    user_values: dict,
    error: BaseException,
    elapsed_ms: int,
) -> None:
    """LLM 호출 실패 시에도 prompt + error 적재 — 사후 디버깅 (silent skip 차단).

    LengthFinishReasonError / 401 / 429 / timeout 같은 폭주 / 예외 케이스에서
    prompt 가 사라지면 원인 분석 불가. except 가 raise 하기 전 호출 — 적재 자체
    실패는 graceful (분석 흐름은 이미 raise 직전이라 막힐 거 없음).

    ★ ``LengthFinishReasonError`` 는 ``completion`` 속성에 partial response 보유 —
    폭주 내용 (반복 패턴 / 쓸데없는 내용) 진단을 위해 추출 적재.
    """
    partial_content = _extract_partial_content(error)
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
            },
            sort_key=sort_key,
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
    """``LengthFinishReasonError`` 의 ``completion`` 속성에서 partial response 추출.

    OpenAI SDK 가 ``.parse()`` 안에서 raise — ``completion.choices[0].message.content`` 가
    폭주 partial 본문. 그 외 예외는 빈 문자열 (graceful).

    폭주 진단용 — 반복 패턴 / 쓸데없는 내용 / 무한 list 등을 사람·LLM 이 확인.
    """
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
