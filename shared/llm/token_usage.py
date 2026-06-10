"""LLM 응답 토큰 사용량 추출 — provider 차이 자동 흡수.

LangChain ``response.usage_metadata`` 표준 사용. provider 별로 다른 응답 형식
(OpenAI / Anthropic / SGLang / vLLM / OpenRouter / Qwen DashScope 등) 을
LangChain 이 정규화해서 ``input_tokens / output_tokens / total_tokens`` +
``input_token_details.cache_read`` 로 통일 제공.

**별도 어댑터 클래스 X — 함수 1개로 끝**.

``cached_tokens`` 가 노출 안 되는 환경 (SGLang ``--enable-cache-report`` OFF 등) 만
``LatencyBasedCacheEstimator`` 로 fallback 추정.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


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


def track_usage(
    response: Any,
    model: str,
    latency_ms: int = 0,
    cache_estimator: Optional["LatencyBasedCacheEstimator"] = None,
) -> TokenUsageRecord:
    """LLM 응답에서 토큰 사용량 추출 — provider 자동 정규화.

    LangChain ``response.usage_metadata`` 표준 사용:
    - input_tokens / output_tokens / total_tokens (필수)
    - input_token_details.cache_read (옵션 — prompt cache hit)

    cached_tokens 노출 X 환경 → ``cache_estimator`` 로 latency 기반 추정.

    Args:
        response: LangChain LLM 응답 (AIMessage 또는 ChatResult).
        model: 모델 이름 — ``llm_pricing.yaml`` 가 비용 계산에 사용.
        latency_ms: LLM 호출 latency (cache_estimator fallback 용).
        cache_estimator: cached_tokens=0 이면 추정 (옵션).

    Returns:
        ``TokenUsageRecord``. ``cost_usd`` 는 0.0 으로 비워둠
        (호출자가 ``llm_pricing.compute_cost`` 로 채움).
    """
    usage_metadata = getattr(response, "usage_metadata", None) or {}
    input_tokens = int(usage_metadata.get("input_tokens", 0))
    output_tokens = int(usage_metadata.get("output_tokens", 0))
    total_tokens = int(usage_metadata.get("total_tokens", input_tokens + output_tokens))

    # prompt cache hit — LangChain 0.3+ 표준 위치
    cache_details = usage_metadata.get("input_token_details", {}) or {}
    cached_tokens = int(cache_details.get("cache_read", 0))

    # fallback: cached_tokens 노출 X 환경
    if cached_tokens == 0 and cache_estimator is not None and input_tokens > 0:
        cached_tokens = cache_estimator.estimate(
            input_tokens=input_tokens,
            latency_ms=latency_ms,
        )

    return TokenUsageRecord(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
    )


class LatencyBasedCacheEstimator:
    """cached_tokens 가 노출 안 되는 환경의 fallback 추정기.

    원리: prompt cache hit 가 많으면 latency 가 짧아짐 (입력 토큰 처리 시간 절감).
    threshold 보다 빠르면 일정 비율 cache hit 로 추정.

    한계: 추정값이라 정확도 낮음. cached_tokens 공식 노출 환경 우선.
    """

    def __init__(
        self,
        fast_latency_ms_per_1k_input: float = 50.0,    # 1k 입력당 50ms 이하 → cache
        cache_hit_ratio: float = 0.85,                 # 빠르면 85% cache 로 가정
    ):
        self.fast_latency_ms_per_1k_input = fast_latency_ms_per_1k_input
        self.cache_hit_ratio = cache_hit_ratio

    def estimate(self, input_tokens: int, latency_ms: int) -> int:
        """latency 기반 cached_tokens 추정."""
        if input_tokens <= 0 or latency_ms <= 0:
            return 0
        ms_per_1k = latency_ms / max(input_tokens / 1000, 0.001)
        if ms_per_1k <= self.fast_latency_ms_per_1k_input:
            return int(input_tokens * self.cache_hit_ratio)
        return 0
