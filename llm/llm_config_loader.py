"""LLM config YAML 로더 — 모델 / 배포 환경별 설정 파일 1개를 dataclass 로 파싱.

설계 원칙
─────────
- 운영자가 ``.env`` 에 ``ROBO_LLM_CONFIG=<name>`` 박으면 ``llm/model_configs/<name>.yaml`` 로딩.
- 누락 필드 / 잘못된 형식 = 명시 ``RuntimeError`` (silent 처리 X).
- API key 는 yaml 에 박지 X — ``.env`` 의 ``ROBO_LLM_API_KEY`` 로 별도.

새 config 추가 = ``llm/model_configs/<name>.yaml`` 1개. 코드 무변경.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
from typing import Any

import yaml


# llm/model_configs/ 위치 — loader와 같은 LLM 책임 루트 아래.
_CONFIGS_DIR = Path(__file__).resolve().parent / "model_configs"
_CONFIG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_REASONING_EFFORTS = frozenset({"", "none", "low", "medium", "high"})


def _require_string(raw: dict, field_name: str, source: str, *, default: str | None = None) -> str:
    if field_name not in raw:
        if default is not None:
            return default
        raise RuntimeError(f"LLM config 필수 필드 누락: {field_name!r} in {source}")
    value = raw[field_name]
    if not isinstance(value, str):
        raise RuntimeError(f"{field_name} must be a string in {source}")
    return value.strip()


def _require_positive_integer(raw: dict, field_name: str, source: str, *, default: int) -> int:
    value = raw.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError(f"{field_name} must be a positive integer in {source}")
    return value


def _require_finite_number(
    raw: dict,
    field_name: str,
    source: str,
    *,
    default: float,
) -> float:
    value = raw.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{field_name} must be a number in {source}")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise RuntimeError(f"{field_name} must be finite in {source}")
    return numeric_value


@dataclass(frozen=True)
class SamplingConfig:
    """ChatOpenAI 표준 sampling — 모든 provider 공통."""
    temperature: float = 0.0
    top_p: float = 1.0


@dataclass(frozen=True)
class LLMConfig:
    """LLM 호출 1회의 설정 묶음 — yaml 1 파일이 이 dataclass 1 인스턴스."""
    # 필수
    provider: str                           # openai / anthropic / openrouter / sglang / vllm / 자유 명명
    model: str                              # 기본 모델 이름

    # provider=anthropic 외 필수 (anthropic 은 SDK default endpoint 사용)
    api_base: str = ""

    # 비용 절감용 작은 모델 — 빈 값이면 model 사용
    model_small: str = ""

    # 토큰 / 타임아웃 / SSL
    max_tokens: int = 8192
    timeout_seconds: int = 300
    verify_ssl: bool = True

    # ChatOpenAI 표준 sampling
    sampling: SamplingConfig = field(default_factory=SamplingConfig)

    # cross-provider reasoning 추상화: none / low / medium / high / 빈 값
    reasoning_effort: str = "none"

    # provider 비표준 인자 (SGLang / vLLM 등) — ChatOpenAI 의 extra_body 인자로 전달
    extra_body: dict[str, Any] = field(default_factory=dict)

    def resolve_small_model(self) -> str:
        """``model_small`` 비어있으면 ``model`` 로 fallback."""
        return self.model_small or self.model


# ─────────────────────────────────────────────────────────────
# 로딩
# ─────────────────────────────────────────────────────────────


def load_llm_config(name: str) -> LLMConfig:
    """``llm/model_configs/<name>.yaml`` 로딩 → ``LLMConfig`` dataclass.

    Args:
        name: config 이름 (확장자 제외). 예: ``"qwen36_sglang_local"``.

    Returns:
        ``LLMConfig`` 인스턴스.

    Raises:
        RuntimeError: name 빈 값 / 파일 없음 / 필수 필드 누락 / 잘못된 형식.
    """
    if not name:
        raise RuntimeError(
            "ROBO_LLM_CONFIG 가 .env 에 설정되지 않음. "
            "llm/model_configs/ 안의 yaml 파일 이름 (확장자 제외) 명시. "
            "예: ROBO_LLM_CONFIG=qwen36_sglang_local"
        )
    if not _CONFIG_NAME_PATTERN.fullmatch(name):
        raise RuntimeError(
            "ROBO_LLM_CONFIG must contain only letters, numbers, underscore, or hyphen"
        )

    path = _CONFIGS_DIR / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in _CONFIGS_DIR.glob("*.yaml"))
        raise RuntimeError(
            f"LLM config 파일 없음: {path}. "
            f"사용 가능한 config: {available!r}. "
            f"새로 만들려면 llm/model_configs/README.md 참조."
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(f"LLM config yaml 파싱 실패: {path}: {e}") from e

    if not isinstance(raw, dict):
        raise RuntimeError(f"LLM config yaml 최상위가 dict 아님: {path} (type={type(raw).__name__})")

    return _build_llm_config(raw, source=str(path))


def _build_llm_config(raw: dict, source: str) -> LLMConfig:
    """yaml dict → ``LLMConfig`` 변환 + 필수 필드 검증."""
    provider = _require_string(raw, "provider", source).lower()
    model = _require_string(raw, "model", source)
    api_base = _require_string(raw, "api_base", source, default="")
    model_small = _require_string(raw, "model_small", source, default="")
    if not provider or not model:
        raise RuntimeError(f"provider and model must be nonempty strings in {source}")

    # anthropic 외는 api_base 필수
    if provider != "anthropic" and not api_base:
        raise RuntimeError(
            f"LLM config 의 provider={provider!r} 일 때 api_base 필수. "
            f"OpenAI 직접이면 'https://api.openai.com/v1', OpenRouter 면 "
            f"'https://openrouter.ai/api/v1', 자체 호스팅이면 해당 endpoint. ({source})"
        )

    sampling_raw = raw.get("sampling", {})
    if not isinstance(sampling_raw, dict):
        raise RuntimeError(f"sampling 이 dict 아님: {source}")

    extra_body = raw.get("extra_body", {})
    if not isinstance(extra_body, dict):
        raise RuntimeError(f"extra_body 가 dict 아님: {source} (type={type(extra_body).__name__})")

    verify_ssl = raw.get("verify_ssl", True)
    if not isinstance(verify_ssl, bool):
        raise RuntimeError(f"verify_ssl must be boolean in {source}")
    max_tokens = _require_positive_integer(raw, "max_tokens", source, default=8192)
    timeout_seconds = _require_positive_integer(raw, "timeout_seconds", source, default=300)
    temperature = _require_finite_number(
        sampling_raw, "temperature", source, default=0.0
    )
    top_p = _require_finite_number(sampling_raw, "top_p", source, default=1.0)
    if temperature < 0.0:
        raise RuntimeError(f"sampling.temperature must be nonnegative in {source}")
    if not 0.0 <= top_p <= 1.0:
        raise RuntimeError(f"sampling.top_p must be between 0 and 1 in {source}")
    reasoning_effort = _require_string(raw, "reasoning_effort", source, default="none").lower()
    if reasoning_effort not in _REASONING_EFFORTS:
        allowed = ", ".join(sorted(_REASONING_EFFORTS - {""}))
        raise RuntimeError(f"reasoning_effort must be blank or one of {allowed} in {source}")

    return LLMConfig(
        provider=provider,
        model=model,
        api_base=api_base,
        model_small=model_small,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        verify_ssl=verify_ssl,
        sampling=SamplingConfig(
            temperature=temperature,
            top_p=top_p,
        ),
        reasoning_effort=reasoning_effort,
        extra_body=dict(extra_body),
    )
