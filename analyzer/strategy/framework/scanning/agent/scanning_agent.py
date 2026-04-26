"""Scanning 에이전트 빌더 — 도구·프롬프트·스킬·미들웨어 조립.

호출자(codebase_profiler) 가 root 와 함께 호출. 단발 호출 가정이라
빌드 함수 자체가 에이전트 객체를 바로 반환.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from langchain.agents import create_agent

from analyzer.strategy.framework.agent.middleware import build_common_middleware
from analyzer.strategy.framework.agent.agent_setup import (
    load_system_prompt,
    load_user_template,
    make_skill_tools,
)
from analyzer.strategy.framework.scanning.agent.scanning_tools import (
    build_tools,
)
from client.llm_client import get_llm
from config.settings import settings

logger = logging.getLogger(__name__)


def build_scanning_agent(root: Path, api_key: str = "") -> tuple[Any, str]:
    """scanning 에이전트 인스턴스 + user_template 반환.

    Returns:
        (agent, user_template)
        - agent: `agent.invoke({"messages": [...]})` 로 호출.
        - user_template: 호출자가 입력 데이터를 채워넣을 템플릿 문자열.
    """
    if not api_key:
        raise RuntimeError(
            "[SCANNING_AGENT] api_key 가 비어있음 — 호출자가 settings.llm.api_key 를 확인 후 전달 필요"
        )
    llm = get_llm(api_key=api_key, model=settings.llm.model)
    tools = build_tools(root) + make_skill_tools("scanning")
    system_prompt = load_system_prompt("scanning")
    user_template = load_user_template("scanning")

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=build_common_middleware(llm),
    )
    return agent, user_template
