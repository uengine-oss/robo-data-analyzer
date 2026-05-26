"""step 함수 진입부 공용 — ``RunnableConfig`` → ``configurable`` dict.

LangGraph step 함수는 모두 같은 보일러플레이트로 ``configurable`` 에서 project /
neo_client / strategy / run / cost_budget / stream 을 꺼낸다. 그 한 줄을 여기로 모은다.
"""
from __future__ import annotations

from typing import Optional

from langchain_core.runnables import RunnableConfig


def step_config(config: Optional[RunnableConfig]) -> dict:
    """``RunnableConfig`` 의 ``configurable`` dict 반환 (없거나 dict 아니면 빈 dict)."""
    return (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
