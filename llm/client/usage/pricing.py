"""모델 → 비용 mapping.

**가격 데이터 단일 진실: ``llm/client/usage/llm_pricing.yaml``** (이 파일은 yaml 로드 + 매칭 로직만).

가격은 1M tokens 당 USD. provider 별로 prefix 가 다를 수 있음:
- OpenRouter: ``"anthropic/claude-sonnet-4"``
- Anthropic 직접: ``"claude-3-5-sonnet-20241022"``
- OpenAI: ``"gpt-4o"``

``lookup_price()`` 매칭 순서:
1. 정확 매칭
2. ★ 날짜 suffix 제거 (``-YYYYMMDD`` 또는 ``-YYYY-MM-DD``) — OpenRouter 등이 자동 추가
3. provider prefix 제거 (``anthropic/`` / ``openai/`` / ``qwen/``)
4. substring 매칭 (마지막 fallback)

가격 변동 시: ``llm_pricing.yaml`` 한 곳만 갱신 + 커밋. 코드 무변경.
갱신 보조: ``python scripts/llm_maintenance/refresh_pricing.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from llm.client.usage.token_usage import TokenUsageRecord
from log.logger import log_warning


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


_PRICING_YAML: Path = Path(__file__).resolve().parent / "llm_pricing.yaml"


def _load_pricing() -> dict[str, ModelPrice]:
    """``llm_pricing.yaml`` → ``{model_name: ModelPrice}``.

    yaml 의 ``models:`` 키 아래 dict 를 ``ModelPrice`` 로 변환.
    `cached_input_per_1m` 누락 시 기본값 0.0 (dataclass 기본값).
    """
    data = yaml.safe_load(_PRICING_YAML.read_text(encoding="utf-8"))
    return {
        name: ModelPrice(**price)
        for name, price in (data.get("models") or {}).items()
    }


# 모델 → 가격 mapping (1M tokens 당 USD).
# 단일 진실 = `llm_pricing.yaml`. 이 dict 는 import 시 yaml 로드 결과.
PRICING: dict[str, ModelPrice] = _load_pricing()


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
                "llm.client.usage.pricing",
                "compute_cost",
                "PRICING dict 에 없는 모델 — 비용 0 으로 집계됨 (측정 사각지대 위험)",
                model=record.model,
                action="add this model to llm/client/usage/llm_pricing.yaml",
            )
        return 0.0
    fresh_input = max(record.input_tokens - record.cached_tokens, 0)
    return (
        (fresh_input        / 1_000_000) * price.input_per_1m
        + (record.cached_tokens / 1_000_000) * price.cached_input_per_1m
        + (record.output_tokens / 1_000_000) * price.output_per_1m
    )
