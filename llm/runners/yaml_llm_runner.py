"""yaml 기반 LLM 호출 헬퍼 — 단순 LLM 호출 (Agent 외부).

용도: ``llm/prompts/<strategy>/<name>.yaml`` 의 룰 로딩 + LLM 호출 + Pydantic 응답.
사용처:
- ``pipeline/steps/step7_analyze_code`` (code_summary / code_table_insights /
  code_rules_examples)
- ``pipeline/steps/step8_post_processing`` (module_summary / table_description)

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

``extends`` 가 있으면 ``_common`` 의 ``system`` 을 그대로 둔다. 기본 user 메시지는 공통 대상
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

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from shared.config.settings import SETTINGS
from llm.usage.cost_budget import CostBudget
from llm.client import build_llm_client
from llm.configs.loader import load_llm_config
from llm.usage.pricing import compute_cost
from llm.usage.token_usage import TokenUsageRecord, UnmeteredLlmCallError, track_usage
from llm.usage.usage_jsonl_writer import persist_usage_and_charge_budget
from shared.events.runtime_logger import log_info, log_warning
from llm.filters import apply_cache_friendly_filters
from llm import response_cache
from llm.repetition_guard import RepetitionGuardCallback, RepetitionLoopAborted
from llm.structured_output import bind_structured_output
from llm.tracing import serialize_response, write_rule_record
from pipeline.execution.run_output_context import get_run_dir


T = TypeVar("T", bound=BaseModel)


from llm.runners.prompt_rules import _load_rule, render_rule_user_content
from llm.runners.prompt_values import _extract_target_and_sort
from llm.runners.invocation import _write_error_record
from llm.runners.transport_retry import (
    MAX_TRANSPORT_ATTEMPTS,
    TRANSPORT_RETRY_DELAYS,
    is_transient_transport_error,
)
from llm.runners.model_output_error import (
    ModelGenerationDeadlineExceeded,
    ModelOutputError,
    model_output_kind,
)

def _resolve_usage_log_path() -> Path:
    """현재 분석 run의 usage 장부. 독립 호출은 설정 루트를 fallback으로 쓴다."""
    return get_run_dir(fallback=SETTINGS.run_output.dir) / "usage.jsonl"


async def run_yaml_rule(
    prompts_dir: Path,
    rule_name: str,
    user_values: dict,
    response_model: type[T] | None,
    budget: Optional[CostBudget] = None,
    model_override: Optional[str] = None,
) -> T | str:
    """yaml 룰 로딩 + LLM 호출 → Pydantic 응답 (또는 평문 텍스트).

    Args:
        prompts_dir: ``strategy.prompts_dir`` (예: ``Path("llm/prompts/framework")``).
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

    wall_timeout = rule.get("wall_timeout_seconds")
    if (
        isinstance(wall_timeout, bool)
        or not isinstance(wall_timeout, (int, float))
        or wall_timeout <= 0
    ):
        if wall_timeout is not None:
            raise ValueError(
                f"wall_timeout_seconds must be a positive number: {rule_name}"
            )
        wall_timeout = None

    system_prompt = rule.get("system", "")
    model = model_override or rule.get("model")

    user_content = render_rule_user_content(rule, user_values)
    response_schema = (
        response_model.model_json_schema() if response_model is not None else None
    )

    # 현재 LLM config — provider 정보를 cache friendly filter 에 전달
    cfg = load_llm_config(SETTINGS.analysis_llm.config)
    structured_output_method = (
        rule.get("structured_output_method") or cfg.structured_output_method
    )

    # 콘텐츠 해시 캐시 — 같은 입력이면 서버를 다시 부르지 않는다(재현성 + 재실행 비용).
    # 키가 프롬프트 전체라서 코드·프롬프트가 바뀌면 자동으로 미적중된다.
    cache_model = model or cfg.model
    cache_start = time.monotonic()
    cache_key = response_cache.make_key(
        rule_name=rule_name,
        system_prompt=system_prompt,
        user_content=user_content,
        model=cache_model,
        max_tokens=rule.get("max_tokens"),
        structured_output_method=structured_output_method,
        response_model=response_model,
    )
    cached = response_cache.load(cache_key, response_model)
    if cached is not None:
        lookup_ms = int((time.monotonic() - cache_start) * 1000)
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
                    cached.model_dump() if hasattr(cached, "model_dump") else cached
                ),
            },
            metadata={
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
    # 반복 폭주 실시간 차단(spec 080) — 감지기가 켜져 있으면 스트리밍 수신으로 전환.
    guard_on = SETTINGS.analysis_llm.repetition_guard
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
    repetition_guard = (
        RepetitionGuardCallback(f"rule:{rule_name}") if guard_on else None
    )
    invoke_config = (
        {"callbacks": [repetition_guard]} if repetition_guard is not None else None
    )

    start = time.monotonic()
    log_info("llm.call", "start", agent=f"rule:{rule_name}")
    attempt = 0
    while True:
        attempt += 1
        try:
            invocation = runnable.ainvoke(messages, config=invoke_config)
            if wall_timeout is None:
                result = await invocation
            else:
                try:
                    result = await asyncio.wait_for(
                        invocation, timeout=float(wall_timeout),
                    )
                except TimeoutError as error:
                    raise ModelGenerationDeadlineExceeded(
                        float(wall_timeout)
                    ) from error
            break
        except Exception as e:  # noqa: BLE001 — explicit retry/failure boundary
            elapsed_ms = int((time.monotonic() - start) * 1000)
            streamed_partial = (
                repetition_guard.detector.tail_text
                if repetition_guard is not None else ""
            )
            # SDK의 stream-idle timeout은 두 종류다. 첫 chunk조차 없으면 전송 실패라
            # 제한 재시도할 수 있지만, 이미 model tool arguments를 받은 뒤 멎은 응답은
            # 불완전한 모델 출력이다. 후자를 같은 content로 재시도하면 120초 stall을
            # 최대 3번 반복하고도 신뢰 가능한 의미는 얻지 못한다.
            partial_stream_stall = (
                type(e).__name__ == "StreamChunkTimeoutError"
                and bool(streamed_partial)
            )
            if (
                is_transient_transport_error(e)
                and not partial_stream_stall
                and attempt < MAX_TRANSPORT_ATTEMPTS
            ):
                _write_error_record(
                    rule_name, system_prompt, user_content, user_values, e, elapsed_ms,
                    response_schema, structured_output_method,
                    streamed_partial=streamed_partial,
                )
                delay = TRANSPORT_RETRY_DELAYS[attempt - 1]
                log_warning(
                    "llm.runners.yaml_llm_runner",
                    "transport_retry",
                    "전송 실패로 동일 요청을 제한 재시도",
                    rule_name=rule_name,
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    delay_sec=delay,
                    error=f"{type(e).__name__}: {e}",
                )
                await asyncio.sleep(delay)
                continue
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
                attempts=attempt,
                elapsed_ms=elapsed_ms,
            )
            _write_error_record(
                rule_name, system_prompt, user_content, user_values, e, elapsed_ms,
                response_schema, structured_output_method,
                streamed_partial=streamed_partial,
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

    if response_model is None:
        raw_msg, parsed, parsing_error = result, result.content, None
    else:
        raw_msg = result.get("raw")
        parsed = result.get("parsed")
        # include_raw=True 는 파싱·검증 실패를 raise 하지 않고 parsing_error 로 돌려준다.
        # 이 사유를 기록·전파하지 않으면 실패 record 가 성공 모양(parsed=None)이 되고,
        # 재시도 계층은 무엇이 틀렸는지 모른 채 동일 요청만 반복한다(spec 116 0단계).
        parsing_error = result.get("parsing_error")
        if parsing_error is None:
            try:
                raw_content = getattr(raw_msg, "content", None)
                if isinstance(raw_content, str) and raw_content.strip():
                    complete_payload = json.loads(raw_content)
                    if complete_payload != parsed:
                        raise ValueError(
                            "provider parsed payload differs from complete raw JSON"
                        )
                    parsed = complete_payload
                parsed = response_model.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError, ValueError) as error:
                parsed = None
                parsing_error = error

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
        )
        raise error

    # 비용 추적 — Agent 외부라 미들웨어 안 붙음. 직접 처리 + usage.jsonl 적재.
    metadata: dict = {
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
                    parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
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
    response_cache.store(cache_key, parsed)
    return parsed
