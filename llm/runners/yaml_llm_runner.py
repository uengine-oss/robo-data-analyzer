"""yaml 기반 LLM 호출 헬퍼 — 단순 LLM 호출 (Agent 외부).

용도: ``pipeline/meaning/<family>/prompts/<strategy>/<name>.yaml`` 의 룰 로딩 + LLM 호출 + Pydantic 응답.
사용처:
- ``pipeline/meaning`` (code_summary / code_rule_meanings)
- ``pipeline/stages/output`` (module_summary / table_description)

yaml 형식 (단순)::

    system: |
      <system prompt — byte-identical 보존>
    user_template: |
      <user 메시지 template (str.format 치환)>
    model: <옵션 — None 이면 현재 llm_config 의 model 사용>

yaml 형식 (DRY — 분할 그룹의 KV 캐시 prefix 통일)::

    # `pipeline/meaning/prompts/common_system.yaml` — 함수 의미 system 단일 진실
    system: |
      <분할 공통 system 본문>
    user_template_inputs: |
      <분할 공통 user_template 입력값 영역>

    # `pipeline/meaning/<family>/prompts/<strategy>/<concern_name>.yaml` — 관심사 고유만
    extends: ../../../prompts/common_system
    user_template_specific: |
      <이 분할 고유 user_template 지침>

``extends`` 가 있으면 지정 YAML의 ``system`` 을 그대로 둔다. 기본 user 메시지는 공통 대상
입력 뒤 분할 계약·전용 증거를 잇는다. 결정론 evidence packet만으로 책임이 완결되는 rule은
``inherit_inputs: false``로 중복 SOURCE를 제외할 수 있다. 이는 값을 숨기는 토큰 절단이 아니라
그 호출이 소비하지 않는 입력 소유권을 schema와 함께 제거하는 명시 계약이다.

config 기본 또는 rule override로 resolve한 구조화 출력 방식으로 provider 문법 강제를 걸고
usage/raw를 확보한 뒤, runner가 원래 Pydantic model로 검증한다. 실제 적용 방식은 모든 prompt
evidence에 기록한다. provider SDK의 streaming ``parsed`` 타입과 내부 모델 타입을 섞지 않는다.

**Plan §4 M0 핵심**: Agent 외부 호출도 ``usage.jsonl`` 적재 (측정 사각지대 차단).
``CostTrackingMiddleware`` 와 같은 헬퍼 ``llm.usage.usage_jsonl_writer.persist_usage_and_charge_budget``
사용.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional, TypeVar

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from shared.config.app_settings import SETTINGS
from llm.usage.cost_budget import CostBudget
from llm.llm_client import build_llm_client
from llm.configs.config_loader import load_llm_config
from llm.usage.model_pricing import compute_cost
from llm.usage.token_usage import TokenUsageRecord, UnmeteredLlmCallError, track_usage
from llm.usage.usage_jsonl_writer import persist_usage_and_charge_budget
from shared.events.runtime_logger import log_info, log_warning
from llm.prompt_filters import apply_cache_friendly_filters
from llm import response_cache
from llm.repetition_guard import RepetitionGuardCallback, RepetitionLoopAborted
from llm.structured_output import bind_structured_output
from llm.request_tracing import serialize_response, write_rule_record
from pipeline.execution.run_output_context import get_run_dir


T = TypeVar("T", bound=BaseModel)


class _RequestTimingCallback(AsyncCallbackHandler):
    """Observe client-visible stream timing without claiming provider internals."""

    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self.first_chunk_at: float | None = None
        self.last_chunk_at: float | None = None
        self.chunk_count = 0

    async def on_llm_new_token(
        self, token: str, *, chunk: Any = None, **kwargs: Any,
    ) -> None:
        now = time.monotonic()
        if self.first_chunk_at is None:
            self.first_chunk_at = now
        self.last_chunk_at = now
        self.chunk_count += 1

    def fields(self) -> dict[str, object]:
        first = self.first_chunk_at
        last = self.last_chunk_at
        return {
            "streaming_observed": first is not None,
            "time_to_first_chunk_ms": (
                int((first - self.started_at) * 1000) if first is not None else None
            ),
            "generation_ms": (
                int((last - first) * 1000)
                if first is not None and last is not None else None
            ),
            "stream_chunk_count": self.chunk_count,
        }


def _request_timing_metadata(
    request_metrics: Mapping[str, object] | None,
    callback: _RequestTimingCallback,
    *,
    model_latency_ms: int,
    validation_ms: int,
) -> dict[str, object]:
    metadata = {
        **dict(request_metrics or {}),
        **callback.fields(),
        "model_latency_ms": model_latency_ms,
        "validation_ms": validation_ms,
    }
    queue_wait = metadata.get("queue_wait_ms")
    if isinstance(queue_wait, (int, float)) and not isinstance(queue_wait, bool):
        metadata["request_total_ms"] = round(
            float(queue_wait) + model_latency_ms + validation_ms, 3,
        )
    candidates: dict[str, float] = {
        "queue_wait": float(queue_wait or 0),
        "validation": float(validation_ms),
    }
    first_chunk = metadata.get("time_to_first_chunk_ms")
    generation = metadata.get("generation_ms")
    if isinstance(first_chunk, (int, float)) and isinstance(generation, (int, float)):
        candidates.update({
            "first_chunk_wait": float(first_chunk),
            "stream_generation": float(generation),
            "stream_finish": max(
                float(model_latency_ms) - float(first_chunk) - float(generation),
                0.0,
            ),
        })
    else:
        candidates["model_total"] = float(model_latency_ms)
    largest_stage, largest_ms = max(candidates.items(), key=lambda item: item[1])
    metadata["largest_observed_stage"] = largest_stage
    metadata["largest_observed_stage_ms"] = round(largest_ms, 3)
    return metadata


from llm.runners.prompt_rules import _load_rule, render_rule_user_content
from llm.runners.prompt_values import _extract_target_and_sort
from llm.runners.model_invocation import _write_error_record
from llm.runners.model_output_error import (
    ModelOutputError,
    model_output_kind,
)

def _resolve_usage_log_path() -> Path:
    """Return the active run's usage ledger; every model call must have an owner."""
    return get_run_dir() / "usage.jsonl"


async def run_yaml_rule(
    prompts_dir: Path,
    rule_name: str,
    user_values: dict,
    response_model: type[T] | None,
    budget: Optional[CostBudget] = None,
    model_override: Optional[str] = None,
    request_metrics: Mapping[str, object] | None = None,
) -> T | str:
    """yaml 룰 로딩 + LLM 호출 → Pydantic 응답 (또는 평문 텍스트).

    Args:
        prompts_dir: ``strategy.prompts_dir`` (예: ``family-owned prompt directory``).
        rule_name: yaml 파일 이름 (확장자 X — "code_summary" 식).
        user_values: user_template 의 ``{placeholder}`` 치환 값.
        response_model: 응답 Pydantic 클래스. **None = 평문 텍스트 모드** — 스칼라 1개짜리
            출력에 JSON 문법 강제를 걸면 약한 모델이 문법상 합법인 공백만 뽑는 폭주가
            가능해서(run7 실측 3만 토큰), 그런 룰은 평문으로 받고 형식은 호출측
            결정론 코드가 검증한다.
        budget: ``CostBudget`` — 호출 후 ``add_sync(cost)``. 임계 초과 시 raise.
        model_override: yaml 의 ``model`` 보다 우선. ``ROBO_LLM_MODEL_SMALL`` 등 전달 가능.

    Returns:
        ``response_model`` 인스턴스 (텍스트 모드는 응답 본문 ``str``).
    """
    rule = _load_rule(prompts_dir, rule_name)

    system_prompt = rule.get("system", "")
    model = model_override or rule.get("model")

    user_content = render_rule_user_content(rule, user_values)
    response_schema = (
        response_model.model_json_schema() if response_model is not None else None
    )
    request_metrics = dict(request_metrics or {})
    if "payload_chars" in request_metrics:
        request_metrics["input_value_chars"] = request_metrics["payload_chars"]
    prompt_chars = len(system_prompt) + len(user_content)
    response_schema_chars = len(
        json.dumps(
            response_schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    ) if response_schema is not None else 0
    request_metrics.update({
        "prompt_chars": prompt_chars,
        "response_schema_chars": response_schema_chars,
        "payload_chars": prompt_chars + response_schema_chars,
    })

    # 현재 LLM config — provider 정보를 cache friendly filter 에 전달
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    structured_output_method = (
        rule.get("structured_output_method") or cfg.structured_output_method
    )

    guard_on = SETTINGS.analysis_llm.repetition_guard
    messages = apply_cache_friendly_filters(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ],
        provider=cfg.provider,
    )

    # 영속 응답 캐시 — 봉인된 실행환경과 실제 전송 설정이 모두 같을 때만 조회한다.
    # 지문이 없으면 정확성을 위해 cache miss가 아니라 cache 자체를 사용하지 않는다.
    cache_model = model or cfg.model
    cache_key: str | None = None
    cached = None
    cache_start = time.monotonic()
    if response_cache.is_enabled():
        requested_max_tokens = rule.get("max_tokens")
        effective_max_tokens = (
            cfg.max_tokens if requested_max_tokens is None else requested_max_tokens
        )
        # Dynamic exact-key response models may rebuild an equivalent schema with
        # different generated definition names on a second call.  Use the schema
        # captured once above for cache identity, provider binding, and tracing.
        cache_key = response_cache.make_key_from_schema(
            rule_name=rule_name,
            system_prompt=str(messages[0].content),
            user_content=str(messages[1].content),
            runtime_sha256=SETTINGS.analysis_llm.model_runtime_capabilities_sha256,
            provider=cfg.provider,
            api_base=cfg.api_base,
            model=cache_model,
            max_tokens=effective_max_tokens,
            temperature=cfg.sampling.temperature,
            top_p=cfg.sampling.top_p,
            reasoning_effort=cfg.reasoning_effort,
            extra_body=cfg.extra_body,
            repetition_guard=guard_on,
            structured_output_method=structured_output_method,
            response_schema=response_schema,
        )
        cached = response_cache.load(cache_key, response_model)
        if (
            cached is None
            and SETTINGS.analysis_llm.response_cache_require_hit
        ):
            error = response_cache.RequiredResponseCacheMiss(
                f"required response cache miss for rule {rule_name!r}; "
                f"request_sha256={cache_key}"
            )
            _write_error_record(
                rule_name,
                system_prompt,
                user_content,
                user_values,
                error,
                int((time.monotonic() - cache_start) * 1000),
                response_schema,
                structured_output_method,
                request_metadata=request_metrics,
            )
            raise error
    if cached is not None:
        lookup_ms = int((time.monotonic() - cache_start) * 1000)
        queue_wait = request_metrics.get("queue_wait_ms")
        queue_wait_value = (
            float(queue_wait)
            if isinstance(queue_wait, (int, float)) and not isinstance(queue_wait, bool)
            else 0.0
        )
        cache_timing: dict[str, object] = {
            **request_metrics,
            "streaming_observed": False,
            "time_to_first_chunk_ms": None,
            "generation_ms": None,
            "stream_chunk_count": 0,
            "model_latency_ms": 0,
            "validation_ms": 0,
            "cache_lookup_ms": lookup_ms,
        }
        if isinstance(queue_wait, (int, float)) and not isinstance(queue_wait, bool):
            cache_timing["request_total_ms"] = round(queue_wait_value + lookup_ms, 3)
        if queue_wait_value >= lookup_ms:
            cache_timing["largest_observed_stage"] = "queue_wait"
            cache_timing["largest_observed_stage_ms"] = queue_wait_value
        else:
            cache_timing["largest_observed_stage"] = "cache_lookup"
            cache_timing["largest_observed_stage_ms"] = lookup_ms
        # 적중도 LLM 호출이다 — 적재를 건너뛰면 "모든 LLM 호출을 usage.jsonl 에 적재"
        # (Plan §4 M0) 가 깨지고 사용량 통계가 호출 수를 과소 보고한다.
        # 서버를 안 불렀으므로 토큰·비용은 0 이고, model 은 키를 만든 요청측 이름이다
        # (응답이 없어 response_metadata 의 이름을 알 수 없다).
        persist_usage_and_charge_budget(
            usage_log_path=_resolve_usage_log_path(),
            agent_name=f"rule:{rule_name}",
            record=TokenUsageRecord(model=cache_model, input_tokens=0, output_tokens=0,
                                    latency_ms=lookup_ms),
            cost_usd=0.0,
            cost_budget=budget,
            response_cache_hit=True,
        )
        target_id, sort_key = _extract_target_and_sort(user_values)
        write_rule_record(
            rule_name=rule_name,
            target_id=target_id,
            system_prompt=system_prompt,
            user_content=user_content,
            response={
                "raw": None,
                "parsed": (
                    cached.model_dump(by_alias=True)
                    if hasattr(cached, "model_dump") else cached
                ),
            },
            metadata={
                **cache_timing,
                "model": cache_model,
                "response_cache_hit": True,
                "latency_ms": lookup_ms,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "cost_usd": 0.0,
                "structured_output_method": (
                    structured_output_method if response_schema is not None else None
                ),
            },
            sort_key=sort_key,
            response_schema=response_schema,
        )
        log_info("llm.call", "cache_hit", agent=f"rule:{rule_name}", elapsed_ms=lookup_ms)
        return cached
    # 반복 폭주 실시간 차단 — 감지기가 켜져 있으면 스트리밍 수신으로 전환.
    llm = build_llm_client(
        model=model, max_tokens=rule.get("max_tokens"), streaming=guard_on
    )
    # include_raw=True — Pydantic 결과 + usage_metadata 동시 획득
    runnable = (
        llm
        if response_model is None
        else bind_structured_output(
            llm, response_schema, method=structured_output_method
        )
    )

    # silent 차단 — cost_tracking 미들웨어 안 거치는 진입점이라 직접 wrap.
    # 예외 (401 / 429 / 400 / timeout / network / LengthFinishReasonError) 시
    # 명시 로그 + prompt 적재 (디버깅 보조) + raise. 적재 누락 = 사후 분석 불가.
    repetition_guard = (
        RepetitionGuardCallback(f"rule:{rule_name}") if guard_on else None
    )

    start = time.monotonic()
    timing_callback = _RequestTimingCallback(start)
    callbacks = [
        callback for callback in (repetition_guard, timing_callback)
        if callback is not None
    ]
    invoke_config = {"callbacks": callbacks}
    log_info("llm.call", "start", agent=f"rule:{rule_name}")
    try:
        result = await runnable.ainvoke(messages, config=invoke_config)
    except Exception as e:  # noqa: BLE001 — explicit single-attempt failure boundary
        elapsed_ms = int((time.monotonic() - start) * 1000)
        streamed_partial = (
            repetition_guard.detector.tail_text
            if repetition_guard is not None else ""
        )
        partial_stream_stall = (
            type(e).__name__ == "StreamChunkTimeoutError"
            and bool(streamed_partial)
        )
        event = (
            "repetition_guard_abort"
            if isinstance(e, RepetitionLoopAborted)
            else "model_error"
        )
        log_warning(
            "llm.runners.yaml_llm_runner",
            event,
            f"LLM 호출 중단 — {type(e).__name__}: {e}",
            rule_name=rule_name,
            attempts=1,
            elapsed_ms=elapsed_ms,
        )
        _write_error_record(
            rule_name, system_prompt, user_content, user_values, e, elapsed_ms,
            response_schema, structured_output_method,
            streamed_partial=streamed_partial,
            request_metadata=_request_timing_metadata(
                request_metrics,
                timing_callback,
                model_latency_ms=elapsed_ms,
                validation_ms=0,
            ),
        )
        if partial_stream_stall:
            raise ModelOutputError(
                rule_name=rule_name,
                kind="stalled",
                detail=str(e),
                original_type=type(e).__name__,
            ) from e
        output_kind = model_output_kind(e)
        if output_kind is not None:
            raise ModelOutputError(
                rule_name=rule_name,
                kind=output_kind,
                detail=str(e),
                original_type=type(e).__name__,
            ) from e
        raise
    latency_ms = int((time.monotonic() - start) * 1000)
    validation_started = time.monotonic()

    if response_model is None:
        raw_msg, parsed, parsing_error = result, result.content, None
    else:
        raw_msg = result.get("raw")
        parsed = result.get("parsed")
        # include_raw=True 는 파싱·검증 실패를 raise 하지 않고 parsing_error 로 돌려준다.
        # 이 사유를 기록·전파하지 않으면 실패 record 가 성공 모양(parsed=None)이 되고,
        # 재시도 계층은 무엇이 틀렸는지 모른 채 동일 요청만 반복한다.
        parsing_error = result.get("parsing_error")
        try:
            raw_content = getattr(raw_msg, "content", None)
            if isinstance(raw_content, str) and raw_content.strip():
                try:
                    complete_payload = json.loads(raw_content)
                except json.JSONDecodeError:
                    if parsing_error is not None:
                        raise parsing_error
                    raise
                if parsed is not None and complete_payload != parsed:
                    raise ValueError(
                        "provider parsed payload differs from complete raw JSON"
                    )
                parsed = complete_payload
                parsing_error = None
            elif parsing_error is not None:
                raise parsing_error
            if parsed is None:
                raise ValueError("parsed payload is missing")
            parsed = response_model.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            parsed = None
            parsing_error = error

    validation_ms = int((time.monotonic() - validation_started) * 1000)

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
            rule_name, system_prompt, user_content, user_values, error, latency_ms,
            response_schema, structured_output_method,
            request_metadata=_request_timing_metadata(
                request_metrics,
                timing_callback,
                model_latency_ms=latency_ms,
                validation_ms=validation_ms,
            ),
        )
        raise error

    # 비용 추적 — Agent 외부라 미들웨어 안 붙음. 직접 처리 + usage.jsonl 적재.
    metadata: dict = {
        **_request_timing_metadata(
            request_metrics,
            timing_callback,
            model_latency_ms=latency_ms,
            validation_ms=validation_ms,
        ),
        "latency_ms": latency_ms,
        "structured_output_method": (
            structured_output_method if response_schema is not None else None
        ),
    }
    if parsing_error is not None:
        metadata["parsing_error"] = f"{type(parsing_error).__name__}: {parsing_error}"
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

        # finish_reason 관측 (가드레일 리서치 #3) — `length` = 종료 실패/절단 신호.
        # MTP 등 서버 변화가 조용한 절단을 유발하면 여기 비율이 급증한다(finish_reason=length grep).
        # 구조화 출력은 length 시 파싱 실패로 예외 경로를 타지만, 성공 콜에도 실어 분포를 남긴다.
        finish_reason = response_meta.get("finish_reason")
        if finish_reason:
            metadata["finish_reason"] = finish_reason

        # analyzer.log 한 줄 — `tail -f` 로 LLM 호출 진행 보임.
        log_info(
            "llm.call",
            "done",
            agent=f"rule:{rule_name}",
            model=model_name,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_tokens=record.cached_tokens or None,
            finish_reason=finish_reason if finish_reason and finish_reason != "stop" else None,
            cost_usd=f"{cost:.6f}",
            elapsed_ms=latency_ms,
        )

        metadata.update({
            "model": model_name,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cached_tokens": record.cached_tokens,
            "cost_usd": cost,
            "response_cache_hit": False,
        })

    # prompt + 응답 <run_dir>/llm-prompts/<rule_name>.jsonl 적재.
    # 호출당 한 줄 append; stable source 정렬은 공용 reader가 read-time에 수행한다.
    # 로깅 실패는 분석 자체 막지 X (graceful) — silent skip 금지, 사유 명시.
    try:
        target_id, sort_key = _extract_target_and_sort(user_values)
        write_rule_record(
            rule_name=rule_name,
            target_id=target_id,
            system_prompt=system_prompt,
            user_content=user_content,
            response={
                # 구조화 응답은 content가 비고 tool_calls에 실제 인자가 들어갈 수 있다.
                # 둘 다 보존해야 parser 전 모델 응답과 parsed 결과를 구분해 감사할 수 있다.
                "raw": serialize_response(raw_msg),
                "parsed": (
                    parsed.model_dump(by_alias=True)
                    if hasattr(parsed, "model_dump") else parsed
                ),
            },
            metadata=metadata,
            sort_key=sort_key,
            response_schema=response_schema,
        )
    except Exception as e:
        log_warning(
            "llm.runners.yaml_llm_runner",
            "write_rule_record",
            "프롬프트 증거 적재 실패 — 증거 없는 분석을 막기 위해 호출 결과 기각",
            rule_name=rule_name,
            error=f"{type(e).__name__}: {e}",
        )
        raise RuntimeError(
            f"prompt evidence write failed for rule {rule_name!r}"
        ) from e

    if finish_reason == "length":
        detail = "provider stopped generation at the configured output-token boundary"
        if parsing_error is not None:
            detail = f"{detail}; {type(parsing_error).__name__}: {parsing_error}"
        raise ModelOutputError(
            rule_name=rule_name,
            kind="length",
            detail=detail,
            original_type="LengthFinishReason",
        )

    if parsed is None:
        reason = (
            f" — {type(parsing_error).__name__}: {parsing_error}"
            if parsing_error is not None
            else ""
        )
        log_warning(
            "llm.runners.yaml_llm_runner",
            "parse_failed",
            "구조화 응답 파싱·검증 실패 — raw 는 위 record 에 보존됨",
            rule_name=rule_name,
            parsing_error=metadata.get("parsing_error"),
        )
        raise ModelOutputError(
            rule_name=rule_name,
            kind="schema",
            detail=(
                f"{type(parsing_error).__name__}: {parsing_error}"
                if parsing_error is not None
                else "parsed payload is missing"
            ),
            original_type=(
                type(parsing_error).__name__ if parsing_error is not None else "MissingParsedPayload"
            ),
        )
    if cache_key is not None:
        response_cache.store(cache_key, parsed)
    return parsed
