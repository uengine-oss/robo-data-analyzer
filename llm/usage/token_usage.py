"""LLM 응답 토큰 사용량 추출 — provider 차이 자동 흡수.

LangChain ``response.usage_metadata`` 표준 사용. provider 별로 다른 응답 형식
(OpenAI / Anthropic / SGLang / vLLM / OpenRouter / Qwen DashScope 등) 을
LangChain 이 정규화해서 ``input_tokens / output_tokens / total_tokens`` +
``input_token_details.cache_read`` 로 통일 제공.

**별도 어댑터 클래스 X — 함수 1개로 끝**.

``cached_tokens`` 가 노출되지 않으면 0으로 기록한다. latency로 token 수를 추측하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class UnmeteredLlmCallError(RuntimeError):
    """LLM 호출 결과에 비용 계측에 필요한 provider usage 정보가 없음."""


class InvalidTokenUsageError(ValueError):
    """비용 계측에 사용할 수 없는 provider token count."""


def _require_nonnegative_token_count(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidTokenUsageError(
            f"{field_name} must be a nonnegative integer token count: {value!r}"
        )
    return value


@dataclass(frozen=True)
class TokenUsageRecord:
    """LLM 호출 1회의 토큰 사용량 + 비용."""
    model: str                               # 모델 이름 (예: "anthropic/claude-sonnet-4")
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0                   # prompt cache hit (RadixAttention 등)
    total_tokens: int = 0
    cost_usd: float = 0.0                    # llm_pricing.compute_cost 가 별도 계산
    latency_ms: int = 0


def validate_token_usage(record: TokenUsageRecord) -> None:
    """비용 계산 전에 ``TokenUsageRecord``의 계측 불변조건을 검증한다."""
    _require_nonnegative_token_count(record.input_tokens, field_name="input_tokens")
    _require_nonnegative_token_count(record.output_tokens, field_name="output_tokens")
    _require_nonnegative_token_count(record.cached_tokens, field_name="cached_tokens")
    _require_nonnegative_token_count(record.total_tokens, field_name="total_tokens")
    if record.cached_tokens > record.input_tokens:
        raise InvalidTokenUsageError(
            "cached_tokens cannot exceed input_tokens: "
            f"cached_tokens={record.cached_tokens}, input_tokens={record.input_tokens}"
        )


def track_usage(
    response: Any,
    model: str,
    latency_ms: int = 0,
) -> TokenUsageRecord:
    """LLM 응답에서 토큰 사용량 추출 — provider 자동 정규화.

    LangChain ``response.usage_metadata`` 표준 사용:
    - input_tokens / output_tokens / total_tokens (필수)
    - input_token_details.cache_read (옵션 — prompt cache hit)

    Args:
        response: LangChain LLM 응답 (AIMessage 또는 ChatResult).
        model: 모델 이름 — ``llm_pricing.yaml`` 가 비용 계산에 사용.
        latency_ms: LLM 호출 latency.

    Returns:
        ``TokenUsageRecord``. ``cost_usd`` 는 0.0 으로 비워둠
        (호출자가 ``llm_pricing.compute_cost`` 로 채움).
    """
    if response is None:
        raise UnmeteredLlmCallError("LLM response is missing; token usage cannot be measured")

    usage_metadata = getattr(response, "usage_metadata", None)
    if not isinstance(usage_metadata, dict):
        raise UnmeteredLlmCallError(
            "LLM response usage_metadata is missing; token usage cannot be measured"
        )
    missing_token_fields = {
        field_name
        for field_name in ("input_tokens", "output_tokens")
        if field_name not in usage_metadata
    }
    if missing_token_fields:
        missing_fields = ", ".join(sorted(missing_token_fields))
        raise UnmeteredLlmCallError(
            f"LLM response usage_metadata is missing required fields: {missing_fields}"
        )

    input_tokens = _require_nonnegative_token_count(
        usage_metadata["input_tokens"], field_name="input_tokens"
    )
    output_tokens = _require_nonnegative_token_count(
        usage_metadata["output_tokens"], field_name="output_tokens"
    )
    total_tokens = _require_nonnegative_token_count(
        usage_metadata.get("total_tokens", input_tokens + output_tokens),
        field_name="total_tokens",
    )

    # prompt cache hit — LangChain 0.3+ 표준 위치
    cache_details = usage_metadata.get("input_token_details", {}) or {}
    cached_tokens = _require_nonnegative_token_count(
        cache_details.get("cache_read", 0), field_name="cached_tokens"
    )
    record = TokenUsageRecord(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
    )
    validate_token_usage(record)
    return record
