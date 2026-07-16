"""Scan Agent — Framework 프로젝트 성격 식별 (단일 에이전트).

시점: ``step3_scan_project`` 단계 (프로젝트당 1회 invoke).

- LangChain ``create_agent`` — 탐색 도구 + 정규식 검증 도구를 든 **단일** 에이전트.
  에이전트가 코드를 둘러보고 SKILL 을 매칭하거나 (SKILL 없으면) 정규식을 직접
  작성·검증해 ``ScanResult`` 를 출력한다. 별도 결정 LLM 없음 — 정규식 같은
  구조물은 검증 도구를 가진 에이전트만 만들 수 있다 (탐색/결정 분리 시 검증된
  정규식이 전달 스키마에서 증발 — 과거 버그).
- skill 은 식별 단서와 ``ScanResult contract``를 가진 문서다. 에이전트가
  ``read_skill``로 읽고, 매칭 시 contract 필드를 변경 없이 ``ScanResult``로 옮긴다.
  코드는 skill 을 파싱하지 않으며 실제 소스 검증으로 부분 추출·미해소를 거부한다.
- 출력 후 코드가 마무리(``_finalize``): 에이전트가 채운 정규식을 ``verify_table_patterns``
  로 결정론 검증해 confidence 산정 — 검증 안 된 정규식은 링킹에 안 넘긴다.
- ``response_format=ScanResult`` Pydantic 강제.
- ``max_calls`` = ``ROBO_AGENT_SCAN_MAX_CALLS``.
- 미들웨어 ``standard_stack`` 명시 주입 (deepagents #2744 회피).
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable, Optional

from data_models.agent_output import ScanResult
from agent_tools.code_inspection import (
    SKILLS_DIR,
    list_skills,
    make_list_code_nodes,
    read_skill,
)
from agent_tools.filesystem import make_filesystem_tools
from analysis_flow.step3_scan_project.scan_verify_tool import make_verify_table_regex_tool
from analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker import (
    PatternVerdict,
    verify_table_patterns,
)
from graph.schema.graph_nodes import CodeNode, Table
from system.env_catalog import ENV
from shared.llm.cost_budget import CostBudget
from shared.llm.structured_agent import run_structured_agent
from shared.log.logger import log_info, log_warning

_STEP = "step3.scan"

# 시스템 프롬프트 위치 — byte-identical 보존 (CI 검증 대상)
_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "prompts" / "agents" / "scan_agent_system.md"
)


def _build_scan_tools(
    project_root: Path, code_nodes: list[CodeNode], tables: list[Table],
) -> list:
    """Scan Agent 도구 9 종 조립.

    파일시스템 5종(ls/glob_search/grep/read_file/read_file_range)은 project_root 에
    묶여 그 밖을 못 본다. list_code_nodes 는 step2 가 파싱한 코드노드 목록을 본다.
    verify_table_regex 는 에이전트가 작성한 정규식을 실제 소스에 검증한다.
    write_todos 는 LangChain TodoListMiddleware 가 자동 제공 (여기 없음).
    """
    return [
        *make_filesystem_tools(project_root).values(),
        make_list_code_nodes(code_nodes),
        list_skills,
        read_skill,
        make_verify_table_regex_tool(project_root, code_nodes, tables),
    ]


async def run_scan_agent(
    project_root: Path,
    scanner_input: dict,
    run_dir: Path,
    cost_budget: CostBudget,
    code_nodes: list[CodeNode],
    tables: list[Table],
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> ScanResult:
    """Scan Agent 1회 invoke → 코드 finalize → ``ScanResult``.

    Args:
        project_root: 분석 대상 디렉토리.
        scanner_input: 사전 수집 단서 (extensions_count / manifest_content / scanner_categories).
        run_dir: ``logs/analysis.run/latest/`` 경로.
        cost_budget: run 1회의 ``CostBudget``.
        code_nodes: step2(load_ast)가 파싱한 CodeNode 목록.
        tables: step1(parse_ddl)의 DDL 테이블 — 정규식 검증 기준.
        stream_emit: 프론트 NDJSON 스트림 emit (옵션).

    Returns:
        ``ScanResult`` — ``table_access_patterns`` 는 검증 통과분만 채워진다.
    """
    draft = await _run_agent(
        project_root=project_root,
        scanner_input=scanner_input,
        run_dir=run_dir,
        cost_budget=cost_budget,
        code_nodes=code_nodes,
        tables=tables,
        stream_emit=stream_emit,
    )
    return await _finalize(draft, project_root, code_nodes, tables)


# ─────────────────────────────────────────────────────────────
# 단계 1 — 에이전트 invoke (탐색 + SKILL 매칭 + 정규식 작성·검증)
# ─────────────────────────────────────────────────────────────


async def _run_agent(
    project_root: Path,
    scanner_input: dict,
    run_dir: Path,
    cost_budget: CostBudget,
    code_nodes: list[CodeNode],
    tables: list[Table],
    stream_emit: Optional[Callable[[dict], None]],
) -> ScanResult:
    """단일 에이전트 invoke → ``ScanResult`` 초안 (공용 ``run_structured_agent``).

    공용 루틴이 ``response_format`` 을 submit 도구로 wrap — agent 가 탐색 끝에
    ``submit_scan`` 호출 시 ScanResult 인스턴스가 state 에 적재된다.
    """
    return await run_structured_agent(
        agent_name="scan",
        invoke_id="run",
        system_prompt=_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
        tools=_build_scan_tools(project_root, code_nodes, tables),
        response_format=ScanResult,
        user_message=_build_user_message(project_root, scanner_input, tables),
        max_calls=ENV.agent.scan_max_calls,
        run_dir=run_dir,
        cost_budget=cost_budget,
        stream_emit=stream_emit,
    )


# ─────────────────────────────────────────────────────────────
# 단계 2 — 코드 finalize (결정론 검증)
# ─────────────────────────────────────────────────────────────


async def _finalize(
    draft: ScanResult,
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
) -> ScanResult:
    """에이전트 draft → 결정론 검증 → 최종 ``ScanResult``.

    정규식은 skill 매칭이든 직접 작성이든 에이전트가 draft 에 직접 채운다 (매칭된 skill은
    ``ScanResult contract``를 변경 없이 복사). 코드는 ``verify_table_patterns`` 로 검증만 —
    통과 못 하면 ``_apply_verdict`` 가 ``no_match_reason`` 으로 표면화한다.
    """
    verdict = await verify_table_patterns(
        patterns=draft.table_access_patterns,
        ddl_transform=draft.ddl_transform,
        project_root=project_root,
        code_nodes=code_nodes,
        tables=tables,
    )
    confidence, no_match_reason = _apply_verdict(
        verdict, draft.table_access_patterns, draft.no_match_reason,
    )

    # codebase_notes — HTML entity (&lt; / &gt; / &amp;) 가 박힌 채 나오면 코드노드 분석
    # LLM 이 그대로 prefix 로 받음. unescape 로 영구 차단 (모든 SKILL 적용).
    return ScanResult(
        matched_skill=draft.matched_skill,
        confidence=confidence,
        evidence=draft.evidence,
        evidence_locations=draft.evidence_locations,
        table_access_patterns=draft.table_access_patterns,
        ddl_transform=draft.ddl_transform,
        codebase_notes=html.unescape(draft.codebase_notes or ""),
        framework_runtime_functions=draft.framework_runtime_functions,
        call_wrappers=draft.call_wrappers,
        no_match_reason=no_match_reason,
    )


def _apply_verdict(
    verdict: PatternVerdict,
    patterns: list,
    no_match_reason: Optional[str],
) -> tuple[float, Optional[str]]:
    """검증 결과 → (confidence, no_match_reason). 실패는 ``log_warning`` 으로 표면화.

    confidence 는 추측이 아니라 측정값 — 검증 통과 시 테이블 해소율 기반.
    """
    if not patterns:
        # 정규식 자체가 없음 — 에이전트가 SKILL 도 못 찾고 작성도 못 함.
        log_warning(
            _STEP, "no_patterns",
            "table_access_patterns 가 비어 있음 — 결정론 R/W 링킹 불가",
            verdict=verdict.summary,
        )
        return 0.1, no_match_reason or "table_access_patterns 미산출"

    if verdict.ok:
        log_info(_STEP, "patterns_verified", result=verdict.summary)
        # confidence = 테이블 해소율(측정값). 검증 자체는 통과했으므로 최소 0.5.
        return max(0.5, round(verdict.resolved_ratio, 2)), no_match_reason

    # 검증 실패 — 정규식이 소스/DDL 과 안 맞음. 표면화.
    log_warning(
        _STEP, "patterns_verify_failed",
        "table_access_patterns 검증 실패 — 정규식이 소스/DDL 과 안 맞음",
        verdict=verdict.summary,
    )
    reason = ((no_match_reason or "") + f" [검증실패: {verdict.summary}]").strip()
    return 0.2, reason


# ─────────────────────────────────────────────────────────────
# user 메시지 조립
# ─────────────────────────────────────────────────────────────


def _build_user_message(project_root: Path, scanner: dict, tables: list[Table]) -> str:
    """scanner 단서 + SKILL 후보 + DDL 테이블 목록을 user 메시지로 직렬화.

    user 메시지 prefix 에 박는 이유:
    - SKILL 후보 — 에이전트가 첫 turn 부터 봄 → 탐색 후 자연스럽게 매칭 진입.
    - DDL 테이블 — verify_table_regex 의 해소 기준 (정규식이 추출한 entity 가 이
      목록과 맞아야 함).
    - RadixAttention 친화 — prefix 가 고정이라 캐시 ↑.
    """
    skills_brief = _list_skills_brief()
    ddl_tables = ", ".join(sorted(t.name for t in tables)) if tables else "(DDL 없음)"
    return "\n".join([
        f"project_root: {project_root}",
        "",
        "scanner_input:",
        json.dumps(scanner, ensure_ascii=False, indent=2),
        "",
        "available_skills (이름: 1줄 설명):",
        skills_brief,
        "",
        "ddl_tables (이 프로젝트가 접근하는 DB 테이블 — verify_table_regex 해소 기준):",
        ddl_tables,
        "",
        "위 단서로 프로젝트 성격을 식별하고 ScanResult 를 출력하라.",
    ])


def _list_skills_brief() -> str:
    """``skills/`` 의 SKILL.md 첫 줄 (h1) 만 직렬 — 에이전트가 후보 보고 자율 판단."""
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
