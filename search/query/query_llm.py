"""검색 질의의 용어집 치환을 한 번 구조화 호출하는 LLM 경계.

분석용 LLM(`ROBO_LLM_CONFIG`)과 **별도** `ROBO_SEARCH_LLM_CONFIG` 를 쓴다(FR-007). 기본은
사내 Qwen(`qwen36_sglang_local`) = GPU 통일(임베딩·번역 모두 사내 서버, 기밀 유지·과금 0).
gpt-5 계열로 바꾸려면 환경변수만 교체(`ROBO_SEARCH_LLM_CONFIG=gpt54_mini_openai`).

스키마·계측·호출 실패는 기록한 뒤 그대로 전파한다. 검색 결과를 제거하는 심판 경로와
수동 JSON 복구 경로는 없다.
"""
from __future__ import annotations

import time
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from shared.config.settings import SETTINGS
from llm.client import build_llm_client_from_config
from llm.configs.loader import load_llm_config
from llm.filters import apply_cache_friendly_filters
from llm.structured_output import bind_structured_output
from llm.usage.pricing import compute_cost
from llm.usage.token_usage import TokenUsageRecord, track_usage
from llm.usage.usage_jsonl_writer import append_usage_record
from shared.events.runtime_logger import log_info, log_warning

_STEP = "search.query.query_llm"
_cfg_cache: dict = {}
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


def _config():
    """검색 LLM 설정과 명시된 인증값을 함께 캐시한다."""
    name = SETTINGS.semantic_search.llm_config
    if name not in _cfg_cache:
        cfg = load_llm_config(name)
        key = SETTINGS.semantic_search.llm_api_key or SETTINGS.analysis_llm.api_key
        _cfg_cache[name] = (cfg, key)
    return _cfg_cache[name]


def _record_usage(response: object, model: str, latency_ms: int) -> TokenUsageRecord:
    """LangChain 표준 usage를 검색 호출 회계에 적재한다."""
    record = track_usage(response, model=model, latency_ms=latency_ms)
    append_usage_record(
        SETTINGS.run_output.dir / "search" / "usage.jsonl",
        agent_name="search",
        record=record,
        cost_usd=compute_cost(record),
    )
    return record


def invoke_structured(
    *,
    system_prompt: str,
    user_prompt: str,
    response_model: type[_ResponseModel],
) -> _ResponseModel:
    """검색 모델을 한 번 호출하고 스키마에 맞는 전체 응답만 받아들인다.

    검색 재작성이 켜진 상태에서 호출·계측·스키마 검증 중 하나라도 실패하면 예외를
    그대로 전달한다. 원질의로 조용히 되돌리는 별도 의미 경로는 만들지 않는다.
    """
    cfg, api_key = _config()
    client = build_llm_client_from_config(cfg, api_key=api_key)
    runnable = bind_structured_output(
        client,
        response_model.model_json_schema(),
        method=cfg.structured_output_method,
    )
    messages = apply_cache_friendly_filters(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ],
        provider=cfg.provider,
    )

    started = time.monotonic()
    log_info(_STEP, "start", model=cfg.model)
    try:
        result = runnable.invoke(messages)
        latency_ms = int((time.monotonic() - started) * 1000)
        raw_response = result.get("raw")
        usage = _record_usage(raw_response, cfg.model, latency_ms)
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            raise parsing_error
        parsed = result.get("parsed")
        if parsed is None:
            raise ValueError("Search LLM structured response is missing parsed data")
        validated = response_model.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001 — 단일 호출 실패 경계의 원인을 보존한다.
        log_warning(
            _STEP,
            "failed",
            f"검색 LLM 구조화 호출 실패: {type(exc).__name__}: {exc}",
            model=cfg.model,
        )
        raise

    log_info(
        _STEP,
        "done",
        model=cfg.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens or None,
        elapsed_ms=usage.latency_ms,
    )
    return validated
