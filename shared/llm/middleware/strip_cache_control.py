"""StripAnthropicCacheControlMiddleware — provider 전환 시 cache_control 누수 차단.

원인: Anthropic 모델용 ``cache_control={"type": "ephemeral"}`` 키가 message 에
박혀있는데 OpenAI 등 다른 provider 로 전환하면 그 키 못 인식하고 에러
(LangChain GitHub #33709).

해결: ``before_model`` 에서 ``message_filters.strip_cache_control`` 호출
(non-Anthropic provider 면 키 제거, Anthropic 이면 그대로).
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware

from shared.config.env_catalog import ENV
from shared.llm.config_loader import load_llm_config
from shared.log.cache_message_filters import strip_cache_control


class StripAnthropicCacheControlMiddleware(AgentMiddleware):
    """non-Anthropic provider 면 message 안 ``cache_control`` 키 자동 제거."""

    def before_model(self, state: Any, runtime: Any) -> Any:
        messages = state.get("messages") if isinstance(state, dict) else None
        if not messages:
            return state
        cfg = load_llm_config(ENV.llm.config)
        strip_cache_control(messages, provider=cfg.provider)
        return state
