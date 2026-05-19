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

from agent_tools.agent_output_models import ProfileFindings, ProfileResult
from agent_tools.code_inspection_tools import (
    SKILLS_DIR,
    list_functions,
    list_skills,
    read_skill,
)
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
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.logging_and_stream.prompt_logger import (
    begin_agent_invoke,
    end_agent_invoke,
)


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
    """Profile Agent 2 단계 호출 → ProfileResult.

    1. **explorer** (create_agent + PROFILE_TOOLS): 도구로 탐색 → ``ProfileFindings``.
    2. **decider** (run_yaml_rule profile_decide): Findings dict 만 받고 깨끗한 컨텍스트에서 ProfileResult 출력.

    누적 도구 결과 (5만 토큰) 가 결정 turn 에 안 흘러감 — 환각 차단 + 토큰 누적 차단.

    Args:
        project_root: 분석 대상 디렉토리.
        scanner_input: 사전 수집한 단서 (extensions_count / manifest_content / scanner_categories).
        run_dir: ``logs/analysis.run/latest/`` 경로.
        cost_budget: run 1회의 ``CostBudget``.
        stream_emit: 프론트 NDJSON 스트림 emit (옵션).

    Returns:
        ``ProfileResult`` — Pydantic 강제 출력.
    """
    findings = await _explore(
        project_root=project_root,
        scanner_input=scanner_input,
        run_dir=run_dir,
        cost_budget=cost_budget,
        stream_emit=stream_emit,
    )
    return await _decide(
        findings=findings,
        prompts_dir=_PROMPTS_DIR,
        cost_budget=cost_budget,
    )


# ─────────────────────────────────────────────────────────────
# 단계 1 — explorer (도구 호출 → ProfileFindings)
# ─────────────────────────────────────────────────────────────


async def _explore(
    project_root: Path,
    scanner_input: dict,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]],
) -> ProfileFindings:
    """탐색 전담 — PROFILE_TOOLS 로 도구 호출 후 ProfileFindings 출력."""
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

    agent = create_agent(
        model=llm_model,
        tools=PROFILE_TOOLS,
        system_prompt=system_prompt,
        middleware=middleware,
        response_format=ProfileFindings,
    )

    user_message = _build_user_message(project_root, scanner_input)

    # invoke 의 모든 turn 을 agent_profile.json 에 누적. finally 로 finalize.
    begin_agent_invoke(agent_name="profile", invoke_id="run", system_prompt=system_prompt)
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_message)]})
    finally:
        end_agent_invoke()

    structured = result.get("structured_response")
    if isinstance(structured, ProfileFindings):
        return structured

    # fallback — 마지막 메시지 본문 JSON 파싱
    last_content = result.get("messages", [])[-1].content if result.get("messages") else "{}"
    return ProfileFindings.model_validate_json(str(last_content))


# ─────────────────────────────────────────────────────────────
# 단계 2 — decider (Findings dict → ProfileResult)
# ─────────────────────────────────────────────────────────────


_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "framework"


async def _decide(
    findings: ProfileFindings,
    prompts_dir: Path,
    cost_budget: CostBudget,
) -> ProfileResult:
    """결정 전담 — yaml rule 단발 호출. 도구 X. 깨끗한 컨텍스트."""
    return await run_yaml_rule(
        prompts_dir=prompts_dir,
        rule_name="profile_decide",
        user_values={"findings_json": findings.model_dump_json(indent=2)},
        response_model=ProfileResult,
        budget=cost_budget,
    )


def _build_user_message(project_root: Path, scanner: dict) -> str:
    """scanner 단서 + SKILL 후보 목록을 user 메시지로 직렬화.

    SKILL 후보 (이름 + 1줄 설명) 를 user 메시지 prefix 에 박는 이유:
    - Agent 가 첫 turn 부터 SKILL 후보를 봄 → Phase A 깊이 파지 않고 Phase B 자연 진입.
    - 결정론 매핑 X — Agent 가 자율 판단으로 `read_skill(name)` 호출 또는 매칭 X 결정.
    - RadixAttention 친화 — prefix 가 고정이라 캐시 ↑.
    """
    skills_brief = _list_skills_brief()
    return "\n".join([
        f"project_root: {project_root}",
        "",
        "scanner_input:",
        json.dumps(scanner, ensure_ascii=False, indent=2),
        "",
        "available_skills (이름: 1줄 설명):",
        skills_brief,
        "",
        "Phase A 시그널 충분히 모았으면 즉시 list_skills / read_skill 로 Phase B 진입.",
        "Phase A 무한 반복 금지. 위 SKILL 후보 중 가장 가까운 1개를 read_skill 후 매칭 여부 판단.",
        "매칭 X 면 Phase C 직접 정규식 작성. 추측 X — 도구 결과만 근거로.",
        "",
        "위 단서를 토대로 프로젝트 성격을 식별하고 ProfileResult 를 출력하라.",
    ])


def _list_skills_brief() -> str:
    """``skills/`` 의 SKILL.md 첫 줄 (h1) 만 직렬 — Agent 가 후보 보고 자율 판단.

    결정론 매핑 X — 코드가 SKILL 1 개 강제 선택 X. 후보 목록만 보여주고
    매칭 / 비매칭 판단은 Agent 자율. ``code_inspection_tools.list_skills`` 와 같은 정책.
    """
    if not SKILLS_DIR.is_dir():
        return "(no skills)"
    items: list[str] = []
    for sub in sorted(SKILLS_DIR.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        md = sub / "SKILL.md"
        if not md.exists():
            continue
        try:
            first_line = md.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        except (OSError, IndexError):
            first_line = ""
        items.append(f"- {sub.name}: {first_line}")
    return "\n".join(items) if items else "(no skills)"
