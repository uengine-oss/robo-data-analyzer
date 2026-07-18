"""step 함수 진입부 공용 — ``RunnableConfig`` → ``configurable`` dict.

LangGraph step 함수는 모두 같은 보일러플레이트로 ``configurable`` 에서 project /
neo_client / strategy / run / cost_budget / stream 을 꺼낸다. 그 한 줄을 여기로 모은다.
"""
from __future__ import annotations

from typing import Optional

from langchain_core.runnables import RunnableConfig


def step_config(config: Optional[RunnableConfig]) -> dict:
    """``RunnableConfig`` 의 ``configurable`` dict 반환.

    설정 자체나 ``configurable``이 미지정이면 빈 dict다. 명시된 값의 구조가 잘못됐으면
    늦은 step 내부 ``.get`` 오류로 흘리지 않고 이 경계에서 거부한다.
    """
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise TypeError(f"RunnableConfig must be a dict; got {type(config).__name__}")
    configurable = config.get("configurable")
    if configurable is None:
        return {}
    if not isinstance(configurable, dict):
        raise TypeError(
            "RunnableConfig.configurable must be a dict; "
            f"got {type(configurable).__name__}"
        )
    return configurable
