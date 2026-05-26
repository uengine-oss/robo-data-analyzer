"""모델 → 비용 mapping.

**새 LLM 모델 추가 시 손대는 곳: 이 파일 1곳만**.

가격은 1M tokens 당 USD. provider 별로 prefix 가 다를 수 있음:
- OpenRouter: ``"anthropic/claude-sonnet-4"``
- Anthropic 직접: ``"claude-3-5-sonnet-20241022"``
- OpenAI: ``"gpt-4o"``

``lookup_price()`` 매칭 순서:
1. 정확 매칭
2. ★ 날짜 suffix 제거 (``-YYYYMMDD`` 또는 ``-YYYY-MM-DD``) — OpenRouter 등이 자동 추가
3. provider prefix 제거 (``anthropic/`` / ``openai/`` / ``qwen/``)
4. substring 매칭 (마지막 fallback)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from shared.llm.token_usage import TokenUsageRecord
from shared.logging_and_stream.logger import log_warning


# 날짜 suffix 정규화 — ``-20260415`` 또는 ``-2026-04-15`` 자동 strip
# OpenRouter 등이 응답 모델 ID 에 자동 추가 (예: ``qwen/qwen3.6-35b-a3b-20260415``).
# PRICING dict 는 base 이름만 유지 → suffix variant 도 자동 매칭.
_DATE_SUFFIX_RE = re.compile(r"-\d{4}(?:-\d{2}-\d{2}|\d{4})$")


# 이미 경고한 unknown 모델 (모듈 수명 동안 1회만 경고 — 운영 로그 도배 방지)
_WARNED_UNKNOWN_MODELS: set[str] = set()


@dataclass(frozen=True)
class ModelPrice:
    """1M tokens 당 USD."""
    input_per_1m: float
    output_per_1m: float
    cached_input_per_1m: float = 0.0         # cache hit 시 (보통 1/10 가격)


# 모델 → 가격 mapping (1M tokens 당 USD, 2026-05 기준)
# 새 모델 추가 = 이 dict 에 1줄.
PRICING: dict[str, ModelPrice] = {
    # Anthropic Claude
    "claude-3-5-sonnet-20241022": ModelPrice(input_per_1m=3.0,  output_per_1m=15.0, cached_input_per_1m=0.30),
    "claude-sonnet-4":             ModelPrice(input_per_1m=3.0,  output_per_1m=15.0, cached_input_per_1m=0.30),
    "claude-opus-4":               ModelPrice(input_per_1m=15.0, output_per_1m=75.0, cached_input_per_1m=1.50),
    "claude-haiku-4-5":            ModelPrice(input_per_1m=0.80, output_per_1m=4.0,  cached_input_per_1m=0.08),

    # OpenAI
    "gpt-4o":                      ModelPrice(input_per_1m=2.50, output_per_1m=10.0),
    "gpt-4o-mini":                 ModelPrice(input_per_1m=0.15, output_per_1m=0.60),
    "gpt-5-nano":                  ModelPrice(input_per_1m=0.05, output_per_1m=0.40),
    "gpt-5-mini":                  ModelPrice(input_per_1m=0.25, output_per_1m=2.00),
    "gpt-5.4-nano":                ModelPrice(input_per_1m=0.20, output_per_1m=1.00),
    "gpt-5.4-mini":                ModelPrice(input_per_1m=0.75, output_per_1m=4.50),

    # Qwen (OpenRouter 또는 자체 호스팅)
    "qwen-3-8b":                   ModelPrice(input_per_1m=0.06, output_per_1m=0.20),
    "qwen-3-32b":                  ModelPrice(input_per_1m=0.30, output_per_1m=0.80),
    "qwen-3-72b":                  ModelPrice(input_per_1m=0.60, output_per_1m=1.20),
    # Qwen 3.6 (35B-A3B, OpenRouter https://openrouter.ai/qwen/qwen3.6-35b-a3b 출시 가격)
    "qwen/qwen3.6-35b-a3b":        ModelPrice(input_per_1m=0.15, output_per_1m=1.0),
    "qwen3.6-35b-a3b":             ModelPrice(input_per_1m=0.15, output_per_1m=1.0),

    # 로컬 / 자체 호스팅 SGLang / vLLM (비용 0)
    "local":                       ModelPrice(input_per_1m=0.0,  output_per_1m=0.0),
    "frentis-ai-model":            ModelPrice(input_per_1m=0.0,  output_per_1m=0.0),
}


def lookup_price(model: str) -> Optional[ModelPrice]:
    """모델 이름 → ModelPrice.

    매칭 순서: 정확 → 날짜 suffix strip → provider prefix 제거 → substring.
    매칭 X 면 None — 호출자가 unknown 경고 출력해야 함.

    날짜 suffix strip (``-20260415`` 등) 은 OpenRouter / Anthropic 직접 API 가
    응답 metadata 의 ``model_name`` 에 release date 를 자동 추가하는 경우 대응.
    PRICING dict 는 base 이름만 유지 → variant 매번 추가할 필요 X (트렁크 녹임).
    """
    if model in PRICING:
        return PRICING[model]
    # 날짜 suffix 제거 (예: "qwen/qwen3.6-35b-a3b-20260415" → "qwen/qwen3.6-35b-a3b")
    no_date = _DATE_SUFFIX_RE.sub("", model)
    if no_date != model and no_date in PRICING:
        return PRICING[no_date]
    # provider prefix 제거 (예: "anthropic/claude-sonnet-4" → "claude-sonnet-4")
    bare = no_date.split("/")[-1] if "/" in no_date else no_date
    if bare in PRICING:
        return PRICING[bare]
    # substring 매칭 (마지막 fallback)
    for key, price in PRICING.items():
        if key in model or key in no_date:
            return price
    return None


def compute_cost(record: TokenUsageRecord) -> float:
    """``TokenUsageRecord`` 의 토큰 수 + model 로 USD 비용 계산.

    cached_tokens 는 ``cached_input_per_1m`` 가격 적용 (Anthropic 등은 보통 1/10).

    매칭 안 되는 모델은 0.0 반환 + ``log_warning`` (모델당 1회) — 측정 사각지대를
    만들지 않기 위해 운영자에게 명시 경고 (Plan §1 $400 사고 재발 방지).
    """
    price = lookup_price(record.model)
    if price is None:
        if record.model not in _WARNED_UNKNOWN_MODELS:
            _WARNED_UNKNOWN_MODELS.add(record.model)
            log_warning(
                "shared.llm.llm_pricing",
                "compute_cost",
                "PRICING dict 에 없는 모델 — 비용 0 으로 집계됨 (측정 사각지대 위험)",
                model=record.model,
                action="add this model to PRICING in shared/llm/llm_pricing.py",
            )
        return 0.0
    fresh_input = max(record.input_tokens - record.cached_tokens, 0)
    return (
        (fresh_input        / 1_000_000) * price.input_per_1m
        + (record.cached_tokens / 1_000_000) * price.cached_input_per_1m
        + (record.output_tokens / 1_000_000) * price.output_per_1m
    )
