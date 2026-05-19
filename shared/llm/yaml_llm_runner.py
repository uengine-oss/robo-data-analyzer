"""yaml 기반 LLM 호출 헬퍼 — 단순 LLM 호출 (Agent 외부).

용도: ``prompts/<strategy>/<name>.yaml`` 의 룰 로딩 + LLM 호출 + Pydantic 응답.
사용처:
- ``analysis_flow/step4_linking/stage2_extract_calls`` (call_extract)
- ``analysis_flow/step6_analyze_function`` (function_summary / function_table_insights /
  function_rules_examples)
- ``analysis_flow/step7_post_processing`` (module_summary / dml_extract / actor_merge /
  table_description)
- ``analysis_flow/step5_build_context/large_function_one_liner``

yaml 형식::

    system: |
      <system prompt — byte-identical 보존>
    user_template: |
      <user 메시지 template (str.format 치환)>
    model: <옵션 — None 이면 현재 llm_config 의 model 사용>

``llm.with_structured_output(response_model, include_raw=True)`` 사용 →
usage_metadata 동시 추출 + Pydantic 자동 파싱.

**Plan §4 M0 핵심**: Agent 외부 호출도 ``usage.jsonl`` 적재 (측정 사각지대 차단).
``CostTrackingMiddleware`` 와 같은 헬퍼 ``shared.llm.usage_jsonl_writer.append_usage_record``
사용.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, TypeVar

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from shared.config.env_catalog import ENV
from shared.llm.cost_budget import CostBudget
from shared.llm.llm_client import build_llm_client
from shared.llm.llm_pricing import compute_cost
from shared.llm.token_usage import track_usage
from shared.llm.usage_jsonl_writer import append_usage_record
from shared.logging_and_stream.logger import log_warning
from shared.logging_and_stream.message_filters import apply_cache_friendly_filters


T = TypeVar("T", bound=BaseModel)


def _resolve_usage_log_path() -> Path:
    """``ENV.log.dir/analysis.run/latest/usage.jsonl`` — Agent 미들웨어와 동일 경로."""
    return ENV.log.dir / "analysis.run" / "latest" / "usage.jsonl"


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
        prompts_dir: ``strategy.prompts_dir`` (예: ``Path("prompts/framework")``).
        rule_name: yaml 파일 이름 (확장자 X — "call_extract" 식).
        user_values: user_template 의 ``{placeholder}`` 치환 값.
        response_model: 응답 Pydantic 클래스.
        budget: ``CostBudget`` — 호출 후 ``add_sync(cost)``. 임계 초과 시 raise.
        model_override: yaml 의 ``model`` 보다 우선. ``ROBO_LLM_MODEL_SMALL`` 등 전달 가능.

    Returns:
        ``response_model`` 인스턴스.
    """
    yaml_path = prompts_dir / f"{rule_name}.yaml"
    rule = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    system_prompt = rule.get("system", "")
    user_template = rule.get("user_template", "")
    model = model_override or rule.get("model")

    user_content = user_template.format(**user_values)

    # 현재 LLM config — provider 정보를 cache friendly filter 에 전달
    cfg = load_llm_config(ENV.llm.config)
    llm = build_llm_client(model=model)
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

    start = time.monotonic()
    result = await structured.ainvoke(messages)
    latency_ms = int((time.monotonic() - start) * 1000)

    raw_msg = result.get("raw")
    parsed = result.get("parsed")

    # 비용 추적 — Agent 외부라 미들웨어 안 붙음. 직접 처리 + usage.jsonl 적재.
    metadata: dict = {"latency_ms": latency_ms}
    if raw_msg is not None:
        response_meta = getattr(raw_msg, "response_metadata", {}) or {}
        model_name = response_meta.get("model_name") or response_meta.get("model")
        if not model_name:
            model_name = model or "unknown"
            log_warning(
                "shared.llm.yaml_llm_runner",
                "track_usage",
                "response_metadata 에서 model 이름 추출 실패 — yaml model 또는 'unknown' 으로 적재됨",
                rule_name=rule_name,
            )

        record = track_usage(raw_msg, model=model_name, latency_ms=latency_ms)
        cost = compute_cost(record)

        # usage.jsonl 적재 (Plan §4 M0 — Agent 미들웨어와 동일 형식).
        append_usage_record(
            usage_log_path=_resolve_usage_log_path(),
            agent_name=f"rule:{rule_name}",
            record=record,
            cost_usd=cost,
        )

        # analyzer.log 한 줄 — `tail -f` 로 LLM 호출 진행 보임.
        from shared.logging_and_stream.logger import log_info
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

        if budget is not None:
            budget.add_sync(cost)        # 임계 초과 시 BudgetExceededError

        metadata.update({
            "model": model_name,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cached_tokens": record.cached_tokens,
            "cost_usd": cost,
        })

    # prompt + 응답 logs/llm_prompts/latest/<rule_name>/NNN.json 적재.
    # 로깅 실패는 호출 자체 성공으로 승격 X (디버깅 보조라 graceful degradation 가능).
    # 단 silent skip 금지 — 사유는 stderr 로 명시.
    try:
        from shared.logging_and_stream.prompt_logger import log_rule_call
        log_rule_call(
            rule_name=rule_name,
            system_prompt=system_prompt,
            user_content=user_content,
            response=parsed if parsed is not None else (raw_msg.content if raw_msg else ""),
            metadata=metadata,
        )
    except Exception as e:
        log_warning(
            "shared.llm.yaml_llm_runner",
            "log_rule_call",
            "프롬프트 로그 적재 실패 — 호출 자체는 성공 (디버깅 보조만 누락)",
            rule_name=rule_name,
            error=f"{type(e).__name__}: {e}",
        )

    if parsed is None:
        raise RuntimeError(f"LLM 응답 파싱 실패: rule={rule_name}")
    return parsed
