"""CacheFriendlyOrderMiddleware — volatile 메시지를 messages 맨 뒤로.

원리:
- RadixAttention / Anthropic prompt cache 는 prefix 공유 시 적중.
- volatile (매번 변동) 메시지가 중간에 있으면 그 이후 prefix 가 다 깨짐.
- volatile 메시지를 맨 뒤로 옮겨 prefix 보존 → cache 적중률 95%+.

표시 방법:
- ``message.additional_kwargs["stability"] = "volatile"`` 인 메시지를 맨 뒤로.
- 다른 메시지는 원래 순서 유지.

구현은 ``message_filters.move_volatile_to_end`` 단일 source 재사용.
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware

from shared.logging_and_stream.message_filters import move_volatile_to_end


class CacheFriendlyOrderMiddleware(AgentMiddleware):
    """volatile 메시지를 messages 맨 뒤로 정렬해 cache prefix 보존."""

    def before_model(self, state: Any, runtime: Any) -> Any:
        messages = state.get("messages") if isinstance(state, dict) else None
        if not messages or len(messages) < 2:
            return state
        state["messages"] = move_volatile_to_end(messages)
        return state
