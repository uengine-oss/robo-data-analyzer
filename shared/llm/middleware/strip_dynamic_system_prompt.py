"""StripDynamicSystemPromptMiddleware — system prompt 의 timestamp / uuid 마스킹.

원인: 시스템 프롬프트에 ``datetime.now()`` / uuid 같은 동적값이 박히면 매 호출마다
달라짐 → 캐시 0% 적중.

해결: ``before_model`` 에서 ``message_filters.mask_dynamic_system_prompt`` 호출.

운영 정책: 애초에 timestamp / uuid 박지 X. CI byte-identical 검증으로 강제.
이 미들웨어는 마지막 안전망.
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware

from shared.logging_and_stream.message_filters import mask_dynamic_system_prompt


class StripDynamicSystemPromptMiddleware(AgentMiddleware):
    """시스템 프롬프트의 timestamp / uuid 마스킹."""

    def before_model(self, state: Any, runtime: Any) -> Any:
        messages = state.get("messages") if isinstance(state, dict) else None
        if not messages:
            return state
        mask_dynamic_system_prompt(messages)
        return state
