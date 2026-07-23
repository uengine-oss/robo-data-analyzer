"""LLM 클라이언트 빌더 — ``llm/configs/<name>.yaml`` 기반.

운영자가 ``.env`` 에 ``ROBO_LLM_CONFIG=<name>`` 만 박으면 끝.
모델 / provider / sampling / 비표준 인자 등은 모두 yaml 안에 있음.

지원 provider:
- ``anthropic`` — Anthropic 직접 (``langchain_anthropic.ChatAnthropic``)
- ``openai`` / ``openrouter`` / ``sglang`` / ``vllm`` / 자유 명명 — OpenAI 호환 (``langchain_openai.ChatOpenAI``)

reasoning_effort 의 cross-provider 매핑 — **표준 키만 코드가 자동 처리**:

- OpenAI o-series  → ``reasoning_effort`` 인자 (low/medium/high 만, none 은 미전달)
- Anthropic 3.7+   → ``thinking={"type":"disabled"}`` (none) / budget=1k/4k/16k (low/med/high)
- 로컬 모델 (SGLang/vLLM) → **yaml 의 ``extra_body`` 에 운영자가 직접 명시**.
  예: ``extra_body.chat_template_kwargs.enable_thinking: false``.
  코드 자동 주입 X — provider 별 비표준 키 화이트리스트 / 분기 X.

새 provider 추가 = ``llm/configs/<name>.yaml`` 1개. 코드 무변경.
"""
from __future__ import annotations

import math
from typing import Any, Optional

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.globals import set_llm_cache
from langchain_openai import ChatOpenAI

from shared.config.settings import SETTINGS
from llm.configs.loader import LLMConfig, load_llm_config


# 운영기 캐시 OFF 강제 (ROBO_CACHE_ENABLED=false 일 때 set_llm_cache(None)).
# import 시점에 1회 실행.
if not SETTINGS.llm_response_cache.enabled:
    set_llm_cache(None)


def build_llm_client(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    streaming: bool = False,
):
    """``llm/configs/<ROBO_LLM_CONFIG>.yaml`` 기반 LangChain 클라이언트 생성.

    Args:
        model: 모델 이름 override (기본 ``cfg.model``). 작은 모델 호출 시
            ``cfg.resolve_small_model()`` 결과 전달 가능.
        temperature: temperature override (기본 ``cfg.sampling.temperature``).
        max_tokens: max_tokens override (기본 ``cfg.max_tokens``).
        streaming: True 면 ainvoke 도 내부적으로 스트리밍 수신·집계한다 — 반복 폭주
            실시간 차단(spec 080)의 전제. OpenAI 호환 경로는 ``stream_usage=True`` 를
            함께 켜서 usage 회계(UnmeteredLlmCallError 계약)를 유지한다.

    Returns:
        LangChain BaseChatModel 인스턴스 (ChatOpenAI 또는 ChatAnthropic).

    Raises:
        RuntimeError: ``ROBO_LLM_CONFIG`` 미설정 / config 파일 없음 / API key 누락 등.
    """
    cfg = load_llm_config(SETTINGS.analysis_llm.config)

    if not SETTINGS.analysis_llm.api_key:
        raise RuntimeError("ROBO_LLM_API_KEY 가 .env 에 설정되지 않음.")

    model = cfg.model if model is None else model
    temperature = cfg.sampling.temperature if temperature is None else temperature
    max_tokens = cfg.max_tokens if max_tokens is None else max_tokens

    if not isinstance(model, str) or not model.strip():
        raise RuntimeError("LLM model override must be a nonempty string")
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(float(temperature))
        or temperature < 0
    ):
        raise RuntimeError("LLM temperature override must be a finite nonnegative number")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise RuntimeError("LLM max_tokens override must be a positive integer")

    if cfg.provider == "anthropic":
        return _build_anthropic_client(cfg, model, temperature, max_tokens, streaming)
    return _build_openai_compatible_client(cfg, model, temperature, max_tokens, streaming)


# ─────────────────────────────────────────────────────────────
# provider 별 빌더
# ─────────────────────────────────────────────────────────────


def _build_anthropic_client(
    cfg: LLMConfig,
    model: str,
    temperature: float,
    max_tokens: int,
    streaming: bool,
) -> ChatAnthropic:
    """ChatAnthropic — Anthropic 직접 API.

    Claude 3.7+ 의 extended thinking 은 ``thinking`` 인자로 제어.
    미지원 모델 (3.5 등) 에선 인자 무시됨.
    """
    kwargs: dict[str, Any] = dict(
        model=model,
        anthropic_api_key=SETTINGS.analysis_llm.api_key or None,
        temperature=temperature,
        top_p=cfg.sampling.top_p,
        max_tokens=max_tokens,
        timeout=cfg.timeout_seconds,
        streaming=streaming,
    )

    thinking = _resolve_anthropic_thinking(cfg.reasoning_effort)
    if thinking is not None:
        kwargs["thinking"] = thinking

    return ChatAnthropic(**kwargs)


def _build_openai_compatible_client(
    cfg: LLMConfig,
    model: str,
    temperature: float,
    max_tokens: int,
    streaming: bool,
) -> ChatOpenAI:
    """ChatOpenAI — OpenAI / OpenRouter / 로컬 모델 (SGLang / vLLM 등 호환).

    표준 인자 (temperature / top_p / max_tokens) + reasoning_effort (o-series) +
    extra_body (로컬 모델 비표준 인자).
    """
    kwargs: dict[str, Any] = dict(
        model=model,
        openai_api_key=SETTINGS.analysis_llm.api_key or None,
        openai_api_base=cfg.api_base or None,
        temperature=temperature,
        top_p=cfg.sampling.top_p,
        max_tokens=max_tokens,
        timeout=cfg.timeout_seconds,
    )
    if streaming:
        # 스트리밍 집계 시에도 최종 usage 를 받도록 stream_usage 필수 —
        # 없으면 track_usage 가 UnmeteredLlmCallError 로 전 호출을 실패시킨다.
        kwargs["streaming"] = True
        kwargs["stream_usage"] = True

    # OpenAI 표준 reasoning_effort — low/medium/high 만 전달, none/"" 은 default.
    if cfg.reasoning_effort in ("low", "medium", "high"):
        kwargs["reasoning_effort"] = cfg.reasoning_effort

    # extra_body 는 yaml 의 운영자 명시만 그대로 통과.
    # 비표준 키 (chat_template_kwargs / top_k / min_p 등) 자동 주입 X — provider 별
    # 호환성 분기는 yaml 의 책임. hosted API (OpenAI / Anthropic 등) 가 모르는 키 받으면
    # 400 BadRequest, silent X (cost_tracking awrap 이 명시 로그).
    if cfg.extra_body:
        kwargs["extra_body"] = dict(cfg.extra_body)

    # 자체 인증서 환경 — verify_ssl=False 면 httpx Client 의 verify=False 주입.
    if not cfg.verify_ssl:
        kwargs["http_client"] = httpx.Client(verify=False)
        kwargs["http_async_client"] = httpx.AsyncClient(verify=False)

    return ChatOpenAI(**kwargs)


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────


def _resolve_anthropic_thinking(reasoning_effort: str) -> Optional[dict]:
    """``reasoning_effort`` → Anthropic ``thinking`` 인자.

    Returns:
        - ``"none"`` → ``{"type": "disabled"}``
        - ``"low"`` / ``"medium"`` / ``"high"`` → ``{"type":"enabled","budget_tokens":N}``
        - 그 외 (빈 값) → None (인자 미전달, provider default)
    """
    if reasoning_effort == "none":
        return {"type": "disabled"}
    budget_map = {"low": 1024, "medium": 4096, "high": 16384}
    if reasoning_effort in budget_map:
        return {"type": "enabled", "budget_tokens": budget_map[reasoning_effort]}
    return None
