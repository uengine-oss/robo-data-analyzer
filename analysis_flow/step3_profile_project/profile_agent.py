"""Profile Agent — Framework 프로젝트 성격 식별.

시점: ``step3_profile_project`` 단계 (프로젝트당 1회 invoke).

- LangChain ``create_agent`` 사용 (원본 ``analyzer/strategy/framework/agent/agent_setup.py``
  와 동일 패턴 — ``deepagents.create_deep_agent`` 아님).
- 도구 8 종 (ls / glob_search / grep / read_file / read_file_range /
  list_functions / list_skills / read_skill)
- ``response_format=ProfileResult`` Pydantic 강제
- ``max_calls=30`` (``ROBO_AGENT_PROFILE_MAX_CALLS``)
- 미들웨어 ``standard_stack`` 명시 주입 (deepagents #2744 회피)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from agent_tools.agent_output_models import ProfileResult
from agent_tools.code_inspection_tools import list_functions, list_skills, read_skill
from agent_tools.filesystem_tools import (
    glob_search,
    grep,
    ls,
    read_file,
    read_file_range,
)
from shared.config.env_catalog import ENV
from shared.config.tool_messages import TOOL_MESSAGES
from shared.llm.cost_budget import CostBudget
from shared.llm.llm_client import build_llm_client
from shared.llm.middleware.standard_stack import build_standard_stack


# Profile Agent 가 사용하는 도구 8 종.
# write_todos 는 LangChain 의 TodoListMiddleware 가 자동 제공.
PROFILE_TOOLS = [
    ls,
    glob_search,
    grep,
    read_file,
    read_file_range,
    list_functions,
    list_skills,
    read_skill,
]


# 시스템 프롬프트 위치 — byte-identical 보존 (CI 검증 대상)
_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "prompts" / "agents" / "profile_agent_system.md"
)


async def run_profile_agent(
    project_root: Path,
    scanner_input: dict,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> ProfileResult:
    """Profile Agent 1회 invoke → ProfileResult.

    Args:
        project_root: 분석 대상 디렉토리.
        scanner_input: 사전 수집한 단서 (extensions_count / manifest_content / scanner_categories).
        run_dir: ``logs/analysis.run/latest/`` 경로.
        cost_budget: run 1회의 ``CostBudget``.
        stream_emit: 프론트 NDJSON 스트림 emit (옵션).

    Returns:
        ``ProfileResult`` — Pydantic 강제 출력.
    """
    system_prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    llm_model = build_llm_client()

    middleware = build_standard_stack(
        agent_name="profile",
        max_calls=ENV.agent.profile_max_calls,
        run_dir=run_dir,
        cost_budget=cost_budget,
        llm_model=llm_model,
        stream_emit=stream_emit,
        tool_messages=TOOL_MESSAGES,
    )

    # LangChain create_agent — 원본 agent_setup.py 와 동일 패턴.
    # system_prompt= 인자 (instructions 아님). middleware list 명시 주입.
    agent = create_agent(
        model=llm_model,
        tools=PROFILE_TOOLS,
        system_prompt=system_prompt,
        middleware=middleware,
        response_format=ProfileResult,
    )

    user_message = _build_user_message(project_root, scanner_input)
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_message)]})

    # response_format 강제 → result["structured_response"] 가 ProfileResult
    structured = result.get("structured_response")
    if isinstance(structured, ProfileResult):
        return structured

    # fallback — 마지막 메시지 본문 JSON 파싱
    last_content = result.get("messages", [])[-1].content if result.get("messages") else "{}"
    return ProfileResult.model_validate_json(str(last_content))


def _build_user_message(project_root: Path, scanner: dict) -> str:
    """scanner 단서를 user 메시지로 직렬화."""
    return "\n".join([
        f"project_root: {project_root}",
        "",
        "scanner_input:",
        json.dumps(scanner, ensure_ascii=False, indent=2),
        "",
        "위 단서를 토대로 프로젝트 성격을 식별하고 ProfileResult 를 출력하라.",
    ])
