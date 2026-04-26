"""Linking 에이전트 빌더 — 도구·프롬프트·스킬·미들웨어 조립.

호출자(entity_linker / unresolved_linker) 가 ToolResultCollector 와 함께 호출.
배치별로 다른 도구 세트를 쓰는 경우 (`batch_startpoint_tables` 등) 위해
factory 형태로 반환 — 같은 LLM·미들웨어 환경 위에서 도구만 갈아끼울 수 있음.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from analyzer.strategy.framework.agent.agent_setup import build_agent_factory
from analyzer.strategy.framework.agent.agent_setup import (
    load_system_prompt,
    load_user_template,
    make_skill_tools,
)
from analyzer.strategy.framework.linking.agent.linking_tools import (
    ToolResultCollector,
    build_tools,
)

logger = logging.getLogger(__name__)


def init_linking_env(
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[Callable[..., Any], str, str]:
    """linking 에이전트 빌드 환경 초기화.

    Returns:
        (build_agent_fn, system_prompt, user_template)
        build_agent_fn(collector, **tool_kwargs) → 에이전트 객체
    """
    factory, _model = build_agent_factory(state, api_key=api_key, run_dir=run_dir)
    system_prompt = load_system_prompt("linking")
    user_template = load_user_template("linking")
    skill_tools = make_skill_tools("linking")

    def build_agent_fn(collector: ToolResultCollector, **tool_kwargs: Any) -> Any:
        """수집기·도구 옵션을 넣어 linking 에이전트 인스턴스를 빌드."""
        tools = build_tools(state, collector, **tool_kwargs) + skill_tools
        return factory(tools=tools, system_prompt=system_prompt)

    return build_agent_fn, system_prompt, user_template
