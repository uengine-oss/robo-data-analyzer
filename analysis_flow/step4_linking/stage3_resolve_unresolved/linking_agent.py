"""Linking Agent — Framework 의 unresolved 케이스 추적 (단일 subagent).

시점: ``stage3_resolve_unresolved``, unresolved 케이스마다 invoke (subagent quarantine).

- LangChain ``create_agent`` 사용 (원본 ``analyzer/strategy/framework/agent/agent_setup.py``
  와 동일 패턴 — ``deepagents.create_deep_agent`` 아님).
- 도구 7 종 (bundle_search_lines / read_file_range / ddl_lookup /
  graph_get_callers / graph_get_callees / graph_create_table_access / graph_create_refer_to)
- ``response_format=LinkingAgentResult`` Pydantic 강제
- ``max_calls=15`` (``ROBO_AGENT_LINKING_MAX_CALLS``)
- 미들웨어 ``standard_stack`` 명시 주입
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from agent_tools.agent_output_models import LinkingAgentResult, UnresolvedCase
from agent_tools.bundle_search_tool import bundle_search_lines
from agent_tools.filesystem_tools import read_file_range
from agent_tools.neo4j_graph_tools import (
    ddl_lookup,
    graph_create_refer_to,
    graph_create_table_access,
    graph_get_callees,
    graph_get_callers,
)
from shared.config.env_catalog import ENV
from shared.config.tool_messages import TOOL_MESSAGES
from shared.llm.cost_budget import CostBudget
from shared.llm.llm_client import build_llm_client
from shared.llm.middleware.standard_stack import build_standard_stack


# Linking Agent 가 사용하는 도구 7종.
# bundle_get_chunk 같은 본문 통째 반환 도구는 폐기 ($400 사고 원인).
LINKING_TOOLS = [
    bundle_search_lines,
    read_file_range,
    ddl_lookup,
    graph_get_callers,
    graph_get_callees,
    graph_create_table_access,
    graph_create_refer_to,
]


_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts" / "agents" / "linking_agent_system.md"
)


async def run_linking_agent(
    case: UnresolvedCase,
    codebase_notes: str,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> LinkingAgentResult:
    """unresolved 케이스 1개 → Linking Agent invoke → ``LinkingAgentResult``.

    invoke 별 quarantine — 메인 컨텍스트 깨끗 유지.
    같은 도구 셋, 시작점 (DDL 테이블 / XML sql_key) 만 case 별 다름.
    """
    system_prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

    llm_model = build_llm_client()

    middleware = build_standard_stack(
        agent_name="linking_subagent",
        max_calls=ENV.agent.linking_max_calls,
        run_dir=run_dir,
        cost_budget=cost_budget,
        llm_model=llm_model,
        stream_emit=stream_emit,
        tool_messages=TOOL_MESSAGES,
    )

    # LangChain create_agent — 원본 agent_setup.py 와 동일 패턴.
    agent = create_agent(
        model=llm_model,
        tools=LINKING_TOOLS,
        system_prompt=system_prompt,
        middleware=middleware,
        response_format=LinkingAgentResult,
    )

    user_message = _build_user_message(case, codebase_notes)
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_message)]})

    structured = result.get("structured_response")
    if isinstance(structured, LinkingAgentResult):
        return structured

    last_content = result.get("messages", [])[-1].content if result.get("messages") else "{}"
    return LinkingAgentResult.model_validate_json(str(last_content))


def _build_user_message(case: UnresolvedCase, codebase_notes: str) -> str:
    """``codebase_notes`` (RadixAttention 캐시) + ``UnresolvedCase`` (volatile)."""
    return "\n".join([
        "codebase_notes:",
        codebase_notes or "(없음)",
        "",
        "unresolved_case:",
        json.dumps(case.model_dump(), ensure_ascii=False, indent=2),
        "",
        "도구로 추적해 매칭 성공 시 graph_create_* 호출, 실패 시 diagnosis 명시.",
    ])
