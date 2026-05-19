"""Linking Agent — Framework 의 unresolved 케이스 추적 (탐색/결정 분리).

시점: ``stage3_resolve_unresolved``, case 마다 1 invoke. case 별 quarantine.

**2 단계 호출**:

1. **explorer** (``create_agent`` + LINKING_EXPLORER_TOOLS): 도구로 case 추적 → ``LinkingFindings``.
2. **decider** (``run_yaml_rule linking_decide``): Findings dict 만 받고 깨끗한 컨텍스트에서 ``LinkingAgentResult`` 출력. ``edges_created`` 배열 (등록 요청 dict 들).
3. **edges 등록**: 코드가 ``LinkingAgentResult.edges_created`` 순회 → collector ``add_table_access`` / ``add_refer_to`` 호출.

누적 도구 결과 (수만 토큰) 가 결정 turn 에 안 흘러감 — 환각 차단 + 토큰 누적 차단.

- explorer 도구 5 종 (bundle_search_lines / read_file_range / ddl_lookup / graph_get_callers / graph_get_callees)
- decider 도구 0 (yaml rule 단발 LLM)
- ``response_format=LinkingFindings`` (explorer) / ``LinkingAgentResult`` (decider) Pydantic 강제
- ``max_calls=15`` (``ROBO_AGENT_LINKING_MAX_CALLS``) — explorer 한정
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from agent_tools.agent_output_models import (
    EdgeRegistration,
    LinkingAgentResult,
    LinkingFindings,
    UnresolvedCase,
)
from agent_tools.bundle_search_tool import bundle_search_lines
from agent_tools.filesystem_tools import read_file_range
from agent_tools.neo4j_graph_tools import (
    ddl_lookup,
    get_edge_collector,
    graph_get_callees,
    graph_get_callers,
)
from shared.config.env_catalog import ENV
from shared.config.tool_messages import TOOL_MESSAGES
from shared.llm.cost_budget import CostBudget
from shared.llm.llm_client import build_llm_client
from shared.llm.middleware.standard_stack import build_standard_stack
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.logging_and_stream.logger import log_warning
from shared.logging_and_stream.prompt_logger import (
    begin_agent_invoke,
    end_agent_invoke,
)


# explorer 도구 5 종 — 탐색 전담. graph_create_* 폐기 (결정 LLM 출력 → 코드 등록).
LINKING_EXPLORER_TOOLS = [
    bundle_search_lines,
    read_file_range,
    ddl_lookup,
    graph_get_callers,
    graph_get_callees,
]


_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts" / "agents" / "linking_agent_system.md"
)

_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts" / "framework"
)


async def run_linking_agent(
    case: UnresolvedCase,
    codebase_notes: str,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> LinkingAgentResult:
    """unresolved 케이스 1개 → 2 단계 호출 → ``LinkingAgentResult``.

    1. explorer 가 도구 호출 + ``LinkingFindings`` 출력 (case 별 quarantine).
    2. decider 가 Findings dict 만 받고 ``LinkingAgentResult`` 출력 (깨끗 컨텍스트).
    3. 코드가 ``edges_created`` 순회 → collector 에 엣지 등록.
    """
    findings = await _explore(
        case=case,
        codebase_notes=codebase_notes,
        run_dir=run_dir,
        cost_budget=cost_budget,
        stream_emit=stream_emit,
    )
    result = await _decide(
        findings=findings,
        cost_budget=cost_budget,
    )
    _register_edges(result.edges_created)
    return result


# ─────────────────────────────────────────────────────────────
# 단계 1 — explorer (도구 호출 → LinkingFindings)
# ─────────────────────────────────────────────────────────────


async def _explore(
    case: UnresolvedCase,
    codebase_notes: str,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]],
) -> LinkingFindings:
    """탐색 전담 — LINKING_EXPLORER_TOOLS 로 도구 호출 후 LinkingFindings 출력."""
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

    agent = create_agent(
        model=llm_model,
        tools=LINKING_EXPLORER_TOOLS,
        system_prompt=system_prompt,
        middleware=middleware,
        response_format=LinkingFindings,
    )

    user_message = _build_user_message(case, codebase_notes)

    # case 별 1 파일 — agent_linking/<case_id>.json. invoke 안 모든 turn 누적.
    begin_agent_invoke(
        agent_name="linking",
        invoke_id=case.case_id,
        system_prompt=system_prompt,
    )
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_message)]})
    finally:
        end_agent_invoke()

    structured = result.get("structured_response")
    if isinstance(structured, LinkingFindings):
        return structured

    # fallback — 마지막 메시지 본문 JSON 파싱
    last_content = result.get("messages", [])[-1].content if result.get("messages") else "{}"
    return LinkingFindings.model_validate_json(str(last_content))


# ─────────────────────────────────────────────────────────────
# 단계 2 — decider (Findings dict → LinkingAgentResult + edges_created)
# ─────────────────────────────────────────────────────────────


async def _decide(
    findings: LinkingFindings,
    cost_budget: CostBudget,
) -> LinkingAgentResult:
    """결정 전담 — yaml rule 단발 호출. 도구 X. 깨끗 컨텍스트."""
    return await run_yaml_rule(
        prompts_dir=_PROMPTS_DIR,
        rule_name="linking_decide",
        user_values={"findings_json": findings.model_dump_json(indent=2)},
        response_model=LinkingAgentResult,
        budget=cost_budget,
    )


# ─────────────────────────────────────────────────────────────
# 단계 3 — 엣지 등록 (decider 출력 → collector)
# ─────────────────────────────────────────────────────────────


def _register_edges(edges: list[EdgeRegistration]) -> None:
    """decider 의 ``edges_created`` 순회 → collector 에 엣지 등록.

    collector 가 ``set_graph_context`` 로 미리 set 돼 있어야 (호출자 책임).
    set 안 됐으면 silent skip 금지 — log_warning 명시.
    """
    collector = get_edge_collector()
    if collector is None:
        log_warning(
            "analysis_flow.step4_linking.stage3_resolve_unresolved.linking_agent",
            "register_edges",
            "edge_collector 미설정 — 엣지 등록 건너뜀 (호출자가 set_graph_context 안 했을 가능성)",
            edges=len(edges),
        )
        return

    for edge in edges:
        if edge.edge_type in ("READS", "WRITES"):
            if not edge.table:
                log_warning(
                    "analysis_flow.step4_linking.stage3_resolve_unresolved.linking_agent",
                    "register_edges",
                    "R/W 엣지인데 table 누락 — 등록 건너뜀",
                    function_id=edge.function_id,
                    edge_type=edge.edge_type,
                )
                continue
            collector.add_table_access(
                function_id=edge.function_id,
                table=edge.table,
                crud=edge.edge_type,
                evidence=edge.evidence,
            )
        elif edge.edge_type == "REFER_TO":
            if not edge.target_type or not edge.target_key:
                log_warning(
                    "analysis_flow.step4_linking.stage3_resolve_unresolved.linking_agent",
                    "register_edges",
                    "REFER_TO 엣지인데 target_type / target_key 누락 — 등록 건너뜀",
                    function_id=edge.function_id,
                )
                continue
            collector.add_refer_to(
                function_id=edge.function_id,
                target_type=edge.target_type,
                target_key=edge.target_key,
                evidence=edge.evidence,
            )


def _build_user_message(case: UnresolvedCase, codebase_notes: str) -> str:
    """``codebase_notes`` (RadixAttention 캐시) + ``UnresolvedCase`` (volatile)."""
    return "\n".join([
        "codebase_notes:",
        codebase_notes or "(없음)",
        "",
        "unresolved_case:",
        json.dumps(case.model_dump(), ensure_ascii=False, indent=2),
        "",
        "도구로 추적해 LinkingFindings 출력. 결정 (LinkingAgentResult) 은 다음 단계의 일.",
    ])
