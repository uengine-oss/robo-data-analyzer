"""ModelCallLimitMiddleware — Agent 의 LLM 호출 횟수 한도.

deepagents 표준:
- Profile Agent ``max_calls=30`` (``ROBO_AGENT_PROFILE_MAX_CALLS``)
- Linking Agent (subagent) ``max_calls=15`` (``ROBO_AGENT_LINKING_MAX_CALLS``)

한도 초과 시 ``ModelCallLimitExceeded`` raise → Agent 작업 중단 (폭주 차단).
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware


class ModelCallLimitExceeded(RuntimeError):
    """LLM 호출 한도 초과 — 폭주 차단."""

    def __init__(self, agent_name: str, max_calls: int):
        super().__init__(f"Agent '{agent_name}' exceeded max_calls={max_calls}")
        self.agent_name = agent_name
        self.max_calls = max_calls


class ModelCallLimitMiddleware(AgentMiddleware):
    """Agent 별 LLM 호출 횟수 한도."""

    def __init__(self, agent_name: str, max_calls: int):
        super().__init__()
        self.agent_name = agent_name
        self.max_calls = max_calls
        self._count = 0

    def before_model(self, state: Any, runtime: Any) -> Any:
        if self._count >= self.max_calls:
            raise ModelCallLimitExceeded(self.agent_name, self.max_calls)
        self._count += 1
        return state
