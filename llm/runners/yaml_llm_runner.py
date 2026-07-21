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


from llm.runners.prompt_rules import _load_rule
from llm.runners.prompt_values import _extract_target_and_sort, _safe_format
from llm.runners.invocation import _write_error_record

def _resolve_usage_log_path() -> Path:
    """``ENV.log.dir/analysis.run/latest/usage.jsonl`` — Agent 미들웨어와 동일 경로."""
    return SETTINGS.run_output.dir / "analysis" / "latest" / "usage.jsonl"


async def run_yaml_rule(
    prompts_dir: Path,
    rule_name: str,
    user_values: dict,
    response_model: type[T],
    budget: Optional[CostBudget] = None,
    model_override: Optional[str] = None,
) -> T:
    """yaml 룰 로딩 + LLM 호출 → Pydantic 응답.

    Args:
        prompts_dir: ``strategy.prompts_dir`` (예: ``Path("llm/prompts/framework")``).
        rule_name: yaml 파일 이름 (확장자 X — "code_summary" 식).
        user_values: user_template 의 ``{placeholder}`` 치환 값.
        response_model: 응답 Pydantic 클래스.
        budget: ``CostBudget`` — 호출 후 ``add_sync(cost)``. 임계 초과 시 raise.
        model_override: yaml 의 ``model`` 보다 우선. ``ROBO_LLM_MODEL_SMALL`` 등 전달 가능.

    Returns:
        ``response_model`` 인스턴스.
    """
    rule = _load_rule(prompts_dir, rule_name)

    system_prompt = rule.get("system", "")
    user_template = rule.get("user_template", "")
    model = model_override or rule.get("model")

    user_content = _safe_format(
        user_template,
        {**rule.get("static_values", {}), **user_values},
    )

    # 현재 LLM config — provider 정보를 cache friendly filter 에 전달
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    llm = build_llm_client(model=model, max_tokens=rule.get("max_tokens"))
    # include_raw=True — Pydantic 결과 + usage_metadata 동시 획득
    structured = llm.with_structured_output(response_model, include_raw=True)

    # 캐시 친화 필터 — 단순 LLM 호출에도 미들웨어와 동일 로직 적용
    messages = apply_cache_friendly_filters(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ],
        provider=cfg.provider,
    )

    # silent 차단 — cost_tracking 미들웨어 안 거치는 진입점이라 직접 wrap.
    # 예외 (401 / 429 / 400 / timeout / network / LengthFinishReasonError) 시
    # 명시 로그 + prompt 적재 (디버깅 보조) + raise. 적재 누락 = 사후 분석 불가.
    start = time.monotonic()
    log_info("llm.call", "start", agent=f"rule:{rule_name}")
    try:
        result = await structured.ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — silent 차단 목적
        elapsed_ms = int((time.monotonic() - start) * 1000)
        log_warning(
            "llm.runners.yaml_llm_runner",
            "model_error",
            f"LLM 호출 실패 (silent 차단) — {type(e).__name__}: {e}",
            rule_name=rule_name,
            elapsed_ms=elapsed_ms,
        )
        _write_error_record(rule_name, system_prompt, user_content, user_values, e, elapsed_ms)
        raise
    latency_ms = int((time.monotonic() - start) * 1000)

    raw_msg = result.get("raw")
    parsed = result.get("parsed")

    if raw_msg is None:
        error = UnmeteredLlmCallError(
            f"yaml rule {rule_name!r} response has no raw AI message; "
            "token usage cannot be measured"
        )
        log_warning(
            "llm.runners.yaml_llm_runner",
            "unmetered_response",
            "LLM response has no raw AIMessage; rejecting unmetered response",
            rule_name=rule_name,
        )
        _write_error_record(
            rule_name, system_prompt, user_content, user_values, error, latency_ms
        )
        raise error

    # 비용 추적 — Agent 외부라 미들웨어 안 붙음. 직접 처리 + usage.jsonl 적재.
    metadata: dict = {"latency_ms": latency_ms}
    if raw_msg is not None:
        response_meta = getattr(raw_msg, "response_metadata", {}) or {}
        model_name = response_meta.get("model_name") or response_meta.get("model")
        if not model_name:
            model_name = model or "unknown"
            log_warning(
                "llm.runners.yaml_llm_runner",
                "track_usage",
                "response_metadata 에서 model 이름 추출 실패 — yaml model 또는 'unknown' 으로 적재됨",
                rule_name=rule_name,
            )

        record = track_usage(raw_msg, model=model_name, latency_ms=latency_ms)
        cost = compute_cost(record)

        # usage.jsonl 적재 (Plan §4 M0 — Agent 미들웨어와 동일 형식).
        persist_usage_and_charge_budget(
            usage_log_path=_resolve_usage_log_path(),
            agent_name=f"rule:{rule_name}",
            record=record,
            cost_usd=cost,
            cost_budget=budget,
        )

        # analyzer.log 한 줄 — `tail -f` 로 LLM 호출 진행 보임.
        log_info(
            "llm.call",
            "done",
            agent=f"rule:{rule_name}",
            model=model_name,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_tokens=record.cached_tokens or None,
            cost_usd=f"{cost:.6f}",
            elapsed_ms=latency_ms,
        )

        metadata.update({
            "model": model_name,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cached_tokens": record.cached_tokens,
            "cost_usd": cost,
        })

    # prompt + 응답 logs/llm_prompts/latest/<rule_name>.json 적재.
    # 같은 rule 호출이 한 .json 의 records 배열에 누적 (소스 라인 순 정렬).
    # 로깅 실패는 분석 자체 막지 X (graceful) — silent skip 금지, 사유 명시.
    try:
        target_id, sort_key = _extract_target_and_sort(user_values)
        write_rule_record(
            rule_name=rule_name,
            target_id=target_id,
            system_prompt=system_prompt,
            user_content=user_content,
            response=parsed if parsed is not None else (raw_msg.content if raw_msg else ""),
            metadata=metadata,
            sort_key=sort_key,
        )
    except Exception as e:
        log_warning(
            "llm.runners.yaml_llm_runner",
            "write_rule_record",
            "프롬프트 로그 적재 실패 — 호출 자체는 성공 (디버깅 보조만 누락)",
            rule_name=rule_name,
            error=f"{type(e).__name__}: {e}",
        )

    if parsed is None:
        raise RuntimeError(f"LLM 응답 파싱 실패: rule={rule_name}")
    return parsed
