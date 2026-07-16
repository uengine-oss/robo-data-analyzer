"""캐시 친화 메시지 필터 — single source of truth.

미들웨어 (``llm/client/middleware/*``) 와 단순 LLM 호출
(``llm/client/runners/yaml_llm_runner.run_yaml_rule``) 이 모두 이 모듈의 함수를 import 해서 사용 → DRY.

3 가지 가공:
1. ``strip_cache_control`` — non-Anthropic provider 면 message content 의
   ``cache_control`` 키 자동 제거 (LangChain #33709 회피).
2. ``mask_dynamic_system_prompt`` — system 메시지의 timestamp / uuid 마스킹
   (``regex_patterns.mask_dynamic_values`` 호출).
3. ``move_volatile_to_end`` — ``additional_kwargs["stability"] == "volatile"`` 메시지를
   맨 뒤로 이동 (RadixAttention prefix 보존).

``apply_cache_friendly_filters`` 가 3 가공을 일괄 적용.
"""
from __future__ import annotations

from typing import Any

from llm.client.prompt_logging.prompt_mask_patterns import mask_dynamic_values


def strip_cache_control(messages: list[Any], provider: str) -> None:
    """non-Anthropic provider 면 message content 의 ``cache_control`` 키 제거 (in-place).

    Args:
        provider: 현재 LLM config 의 provider — anthropic 이면 cache 활용 위해 보존.
    """
    if provider.lower() == "anthropic":
        return
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                block.pop("cache_control", None)


def mask_dynamic_system_prompt(messages: list[Any]) -> None:
    """system 메시지의 timestamp / uuid 를 마스킹 (in-place).

    byte-identical 강제 — CI 검증의 마지막 안전망.
    """
    for msg in messages:
        msg_type = getattr(msg, "type", None) or msg.__class__.__name__.lower()
        if msg_type not in ("system", "systemmessage"):
            continue
        content = getattr(msg, "content", None)
        if not isinstance(content, str):
            continue
        cleaned = mask_dynamic_values(content)
        if cleaned != content:
            msg.content = cleaned


def move_volatile_to_end(messages: list[Any]) -> list[Any]:
    """``additional_kwargs["stability"] == "volatile"`` 메시지를 맨 뒤로 정렬.

    Returns:
        새 list (volatile 가 있으면). 없으면 원본 그대로.
    """
    stable: list[Any] = []
    volatile: list[Any] = []
    for msg in messages:
        kwargs = getattr(msg, "additional_kwargs", {}) or {}
        if kwargs.get("stability") == "volatile":
            volatile.append(msg)
        else:
            stable.append(msg)
    return stable + volatile if volatile else messages


def apply_cache_friendly_filters(messages: list[Any], provider: str) -> list[Any]:
    """3 가공 일괄 적용 — 단순 LLM 호출 (Agent 외부) 이 미들웨어와 동일 결과 얻음.

    Args:
        messages: SystemMessage / HumanMessage / AIMessage 등 LangChain Message list.
        provider: 현재 LLM config 의 provider — ``strip_cache_control`` 분기에 사용.

    Returns:
        가공된 메시지 list (in-place + 순서 변경 결과).
    """
    strip_cache_control(messages, provider)
    mask_dynamic_system_prompt(messages)
    return move_volatile_to_end(messages)
