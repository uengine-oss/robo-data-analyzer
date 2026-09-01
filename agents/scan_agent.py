"""Scan Agent — Framework 프로젝트 성격 식별 (단일 에이전트).

시점: ``step4_scan_project`` 단계 (프로젝트당 1회 invoke).

- LangChain ``create_agent`` — 탐색 도구 + 정규식 검증 도구를 든 **단일** 에이전트.
  에이전트가 코드를 둘러보고 SKILL 을 매칭하거나 (SKILL 없으면) 정규식을 직접
  작성·검증해 ``ScanResult`` 를 출력한다. 별도 결정 LLM 없음 — 정규식 같은
  구조물은 검증 도구를 가진 에이전트만 만들 수 있다 (탐색/결정 분리 시 검증된
  정규식이 전달 스키마에서 증발 — 과거 버그).
- skill 은 식별 단서와 ``ScanResult contract``를 가진 문서다. 에이전트는 실제 코드와
  맞는 skill 이름만 선택하고, 코드는 원문 contract를 직접 hydrate한 뒤 실제 소스 검증으로
  부분 추출·미해소를 거부한다. 결정론 계약을 LLM이 다시 받아쓰지 않는다.
- 출력 후 코드가 마무리(``_finalize``): 에이전트가 제안한 규칙을
  ``verify_table_patterns``로 결정론 검증한다. 검증 안 된 규칙은 링킹에 넘기지 않는다.
- LLM 경계는 ``ScanProposal``이고, 내부 ``ScanResult``는 Analyzer만 만든다.
- 공통 계측·캐시 미들웨어 ``standard_stack`` 명시 주입.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from agents import AGENTS_PACKAGE_DIR
from agents.agent_contracts import (
    ApiContract,
    CallWrapper,
    DdlTransform,
    ScanProposal,
    ScanResult,
    TableAccessPattern,
)
from agents.rule_compiler import (
    compile_exclusion_rules,
    compile_rules,
    parse_exclusion_rule,
    parse_rule,
    split_rules,
)
from agents.tools.inspect_tools import (
    build_skill_list,
    list_skills,
    make_list_code_nodes,
    read_skill,
)
from agents.skills.skill_loader import FRAMEWORK_SKILLS_DIR, load_skill_contract
from agents.tools.file_tools import make_filesystem_tools
from agents.tools.verify_tools import make_verify_table_regex_tool
from pipeline.stages.links.tables.regex_table_linker import (
    PatternVerdict,
    PreparedLinkSources,
    prepare_link_sources,
    verify_table_patterns,
)
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.source_languages import ROUTINE_LABELS
from shared.config.app_settings import SETTINGS
from llm.usage.cost_budget import CostBudget
from agents.agent_runtime import AgentModelOutputError, run_structured_agent
from shared.events.runtime_logger import log_info, log_warning
from shared.files.run_cache_context import read_run_text

_STEP = "step4.scan"

# 시스템 프롬프트 위치 — byte-identical 보존 (CI 검증 대상)
_SYSTEM_PROMPT_PATH = AGENTS_PACKAGE_DIR / "prompts" / "scan.md"


class SkillSelectionAmbiguityError(RuntimeError):
    """More than one registered SKILL passed the same production contract."""


class ScanCandidateRejected(AgentModelOutputError):
    """Scan Agent 후보가 결정론 source/DDL 검증을 통과하지 못함."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="semantic")


@dataclass(frozen=True)
class _VerifiedSkillCandidate:
    draft: ScanResult
    merged_patterns: list[TableAccessPattern]
    embedded_rules: list
    verdict: PatternVerdict


def _build_scan_tools(
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
    allowed_source_paths: tuple[Path, ...] | None = None,
) -> list:
    """Scan Agent 도구 9 종 조립.

    파일시스템 5종(ls/glob_search/grep/read_file/read_file_range)은 project_root 에
    묶여 그 밖을 못 본다. list_code_nodes 는 step2 가 파싱한 코드노드 목록을 본다.
    verify_table_regex 는 에이전트가 작성한 정규식을 실제 소스에 검증한다.
    """
    return [
        *make_filesystem_tools(
            project_root,
            allowed_files=allowed_source_paths,
        ).values(),
        make_list_code_nodes(code_nodes, project_root=project_root),
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
    allowed_source_paths: tuple[Path, ...] | None = None,
) -> ScanResult:
    """Scan Agent 1회 invoke → 코드 finalize → ``ScanResult``.

    Args:
        project_root: 분석 대상 디렉토리.
        scanner_input: 전체 확장자·카테고리 건수와 매니페스트 상대경로.
        run_dir: run 로그 디렉토리 (``logs/``).
        cost_budget: run 1회의 ``CostBudget``.
        code_nodes: step2(load_ast)가 파싱한 CodeNode 목록.
        tables: step1(parse_ddl)의 DDL 테이블 — 정규식 검증 기준.
        stream_emit: 프론트 NDJSON 스트림 emit (옵션).

    Returns:
        ``ScanResult`` — ``table_access_patterns`` 는 검증 통과분만 채워진다.
    """
    known = await _select_verified_skill(project_root, code_nodes, tables)
    if known is not None:
        return known

    log_info(_STEP, "known_skill_not_found", action="unknown/custom Agent 탐색")
    proposal = await _run_agent(
        project_root=project_root,
        scanner_input=scanner_input,
        run_dir=run_dir,
        cost_budget=cost_budget,
        code_nodes=code_nodes,
        tables=tables,
        stream_emit=stream_emit,
        allowed_source_paths=allowed_source_paths,
    )
    try:
        hydrated = _hydrate_skill_contract(_proposal_to_scan_result(proposal))
    except ValueError as error:
        raise ScanCandidateRejected(str(error)) from error
    return await _finalize(hydrated, project_root, code_nodes, tables)


def _hydrate_skill_contract(draft: ScanResult) -> ScanResult:
    """매칭된 SKILL 원문 계약을 검증 전에 ScanResult에 결정론 주입한다.

    LLM은 어떤 SKILL이 실제 코드와 맞는지만 판단한다. 문서에 이미 있는 규칙·wrapper·
    런타임 계약을 다시 받아쓰게 하지 않으며, 이 함수가 만든 최종값을 ``_finalize``가
    실제 소스/DDL에 검증한다.
    """
    matched = draft.matched_skill
    if not matched:
        return draft
    contract = load_skill_contract(matched)
    if not contract:
        raise ValueError(f"matched SKILL에 ScanResult contract가 없음: {matched}")

    data = draft.model_dump()
    data.update(_skill_owned_values(contract))
    log_info(_STEP, "skill_contract_hydrated", skill=matched, keys=sorted(contract))
    return ScanResult.model_validate(data)


def _skill_owned_values(contract: dict) -> dict:
    """Parse every SKILL-owned field, making absent keys explicit defaults."""
    return {
        "table_access_rules": [
            parse_rule(raw) for raw in contract.get("table_access_rules") or []
        ],
        "table_access_patterns": [
            TableAccessPattern(**raw)
            for raw in contract.get("table_access_patterns") or []
        ],
        "table_access_exclusion_rules": [
            parse_exclusion_rule(raw)
            for raw in contract.get("table_access_exclusion_rules") or []
        ],
        "ddl_transform": DdlTransform(**(
            contract.get("ddl_transform") or {"ddl_naming_rule": "as_is"}
        )),
        "call_wrappers": [
            CallWrapper(**raw) for raw in contract.get("call_wrappers") or []
        ],
        "framework_runtime_functions": [
            str(name) for name in contract.get("framework_runtime_functions") or []
        ],
        "handler_markers": [
            str(name) for name in contract.get("handler_markers") or []
        ],
        "api_contracts": [
            ApiContract(**raw) for raw in contract.get("api_contracts") or []
        ],
    }


def _proposal_to_scan_result(proposal: ScanProposal) -> ScanResult:
    """Assign Analyzer-owned rule identities to an otherwise identity-free proposal."""
    data = proposal.model_dump(exclude={"table_access_rules", "table_access_patterns"})
    data["table_access_rules"] = [
        parse_rule({"id": f"S{index}", **rule.model_dump()})
        for index, rule in enumerate(proposal.table_access_rules, start=1)
    ]
    data["table_access_patterns"] = [
        TableAccessPattern(id=f"P{index}", **pattern.model_dump())
        for index, pattern in enumerate(proposal.table_access_patterns, start=1)
    ]
    if (
        proposal.matched_skill is None
        and not proposal.table_access_rules
        and not proposal.table_access_patterns
    ):
        data.update({
            "table_access_status": "unavailable",
            "no_match_reason": "model proposed no table access rule",
        })
    return ScanResult.model_validate(data)


def _iter_skill_contracts() -> list[tuple[str, dict]]:
    """Return registered framework contracts in stable order."""
    if not FRAMEWORK_SKILLS_DIR.is_dir():
        return []
    return [
        (directory.name, load_skill_contract(directory.name))
        for directory in sorted(FRAMEWORK_SKILLS_DIR.iterdir(), key=lambda path: path.name)
        if directory.is_dir()
        and not directory.name.startswith("_")
        and directory.name != "templates"
        and (directory / "SKILL.md").is_file()
    ]


async def _select_verified_skill(
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
) -> ScanResult | None:
    """Select one registered SKILL using its production source/DDL contract."""
    candidates: list[_VerifiedSkillCandidate] = []
    prepared_sources: PreparedLinkSources | None = None
    for skill_name, contract in _iter_skill_contracts():
        if not contract:
            continue
        owned = _skill_owned_values(contract)
        compilable, embedded = split_rules(owned["table_access_rules"])
        merged_patterns = compile_rules(compilable) + owned["table_access_patterns"]
        exclusion_patterns = compile_exclusion_rules(
            owned["table_access_exclusion_rules"]
        )
        if not merged_patterns and not embedded:
            continue
        if prepared_sources is None:
            prepared_sources = prepare_link_sources(project_root, code_nodes)
        verdict = await verify_table_patterns(
            patterns=merged_patterns,
            exclusion_patterns=exclusion_patterns,
            ddl_transform=owned["ddl_transform"],
            project_root=project_root,
            code_nodes=code_nodes,
            tables=tables,
            embedded_rules=embedded,
            prepared_sources=prepared_sources,
            require_each_access_rule_match=False,
            require_each_exclusion_rule_match=False,
            require_local_table_resolution=False,
        )
        if not verdict.ok:
            continue
        validate_framework_runtime_contract(
            owned["framework_runtime_functions"], code_nodes,
        )
        draft = ScanResult(
            matched_skill=skill_name,
            **owned,
        )
        candidates.append(_VerifiedSkillCandidate(
            draft=draft,
            merged_patterns=merged_patterns,
            embedded_rules=embedded,
            verdict=verdict,
        ))

    if len(candidates) > 1:
        names = ", ".join(candidate.draft.matched_skill or "" for candidate in candidates)
        raise SkillSelectionAmbiguityError(
            "multiple registered SKILL contracts verified: " + names
        )
    if not candidates:
        return None

    selected = candidates[0]
    log_info(
        _STEP,
        "known_skill_selected",
        skill=selected.draft.matched_skill,
        resolved_ratio=selected.verdict.resolved_ratio,
        llm_calls=0,
    )
    return _complete_finalization(
        selected.draft,
        selected.merged_patterns,
        selected.verdict,
        embedded_rules=selected.embedded_rules,
    )


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
    allowed_source_paths: tuple[Path, ...] | None = None,
) -> ScanProposal:
    """단일 에이전트 invoke → 검증 전 ``ScanProposal``.

    공용 루틴이 ``response_format`` 을 submit 도구로 wrap — agent 가 탐색 끝에
    ``submit_scan`` 호출 시 ScanProposal 인스턴스가 state 에 적재된다.
    """
    return await run_structured_agent(
        agent_name="scan",
        invoke_id="run",
        system_prompt=read_run_text(_SYSTEM_PROMPT_PATH, encoding="utf-8"),
        tools=_build_scan_tools(
            project_root,
            code_nodes,
            tables,
            allowed_source_paths,
        ),
        response_format=ScanProposal,
        user_message=_build_user_message(scanner_input, tables),
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
    """에이전트 draft → 규칙 컴파일 합류 → 결정론 검증 → 최종 ``ScanResult``.

    DSL 규칙(``table_access_rules``)은 여기서 코드가 ``call_regex`` 로 컴파일해 raw 패턴
    목록에 합류시킨다 (spec 108 — named group 구조 보장, 검증 관문은 하나).
    코드는 ``verify_table_patterns`` 로 검증만 한다. 제안한 규칙 하나라도 실패하면
    ``_apply_verdict`` 가 전체 응답을 거절한다. 컴파일/내용 실패는 즉시 raise한다.
    """
    try:
        compilable, embedded = split_rules(draft.table_access_rules)
        merged_patterns = compile_rules(compilable) + draft.table_access_patterns
        exclusion_patterns = compile_exclusion_rules(draft.table_access_exclusion_rules)
        validate_framework_runtime_contract(
            draft.framework_runtime_functions, code_nodes,
        )
    except ValueError as error:
        raise ScanCandidateRejected(str(error)) from error
    verdict = await verify_table_patterns(
        patterns=merged_patterns,
        exclusion_patterns=exclusion_patterns,
        ddl_transform=draft.ddl_transform,
        project_root=project_root,
        code_nodes=code_nodes,
        tables=tables,
        embedded_rules=embedded,
    )
    return _complete_finalization(
        draft, merged_patterns, verdict, embedded_rules=embedded,
    )


def _complete_finalization(
    draft: ScanResult,
    merged_patterns: list[TableAccessPattern],
    verdict: PatternVerdict,
    *,
    embedded_rules: list | tuple = (),
) -> ScanResult:
    """Apply one already-produced verdict without rescanning source files."""
    access_rules = [*merged_patterns, *embedded_rules]
    if not access_rules:
        return _isolate_table_access_failure(
            draft,
            draft.no_match_reason or "table_access_patterns 미산출",
        )
    if not verdict.ok and draft.matched_skill:
        return _isolate_table_access_failure(
            draft,
            "table access rule verification failed: " + verdict.summary,
        )
    no_match_reason = _require_verified_table_access(verdict, access_rules)

    # Copy the complete validated contract, then replace only fields finalized
    # here. A handwritten constructor previously dropped hydrated handler/API
    # facts whenever ScanResult gained or retained fields not listed here.
    data = draft.model_dump()
    data.update({
        "table_access_status": "completed",
        "table_access_patterns": [pattern.model_dump() for pattern in merged_patterns],
        "no_match_reason": no_match_reason,
    })
    return ScanResult.model_validate(data)


def _isolate_table_access_failure(draft: ScanResult, reason: str) -> ScanResult:
    """Fail closed for R/W without erasing independent matched-SKILL facets.

    Table patterns, exclusions, and DDL transforms form one validation facet.  A
    failure there must produce no R/W edges, but it does not invalidate document-
    owned handler, API, runtime, or call-wrapper contracts from the same selected
    SKILL.  Unmatched custom output still fails as one untrusted candidate in
    ``_complete_finalization``.
    """
    log_warning(
        _STEP,
        "table_access_unavailable",
        "table access 계약만 미가용으로 격리하고 독립 SKILL 계약은 보존",
        skill=draft.matched_skill,
        reason=reason,
    )
    data = draft.model_dump()
    data.update({
        "table_access_status": "unavailable",
        "table_access_rules": [],
        "table_access_patterns": [],
        "table_access_exclusion_rules": [],
        "ddl_transform": DdlTransform(ddl_naming_rule="as_is").model_dump(),
        "no_match_reason": reason,
    })
    return ScanResult.model_validate(data)


def validate_framework_runtime_contract(
    patterns: list[str], code_nodes: list[CodeNode],
) -> None:
    """Reject runtime-drop patterns that claim a workspace-defined routine.

    루틴 정의(``ROUTINE_LABELS``)만 충돌 후보다. AST statement 노드의 name 은 루틴이
    아니다 — `goto PFM_CATCH;` 의 GOTO 라벨 이름이 skill 런타임 함수 선언과 거짓
    충돌해 run 전체가 fatal 나던 실측 결함(framework corpus, 2026-08-05).
    """
    def _field(node, key: str) -> str:
        value = node.get(key) if isinstance(node, dict) else getattr(node, key, "")
        return str(value or "").strip()

    names = sorted({
        _field(node, "name")
        for node in code_nodes
        if _field(node, "name") and _field(node, "label") in ROUTINE_LABELS
    })
    conflicts = sorted({
        (pattern, name)
        for pattern in patterns
        for name in names
        if fnmatch.fnmatchcase(name, pattern)
    })
    if conflicts:
        detail = ", ".join(
            f"{pattern!r}->{name!r}" for pattern, name in conflicts[:20]
        )
        raise ValueError(
            "framework_runtime_functions conflicts with project routines: " + detail
        )


def _require_verified_table_access(
    verdict: PatternVerdict,
    access_rules: list,
) -> Optional[str]:
    """검증된 접근 규칙만 받아들이고 실패한 제안은 즉시 거절한다."""
    if not access_rules:
        # 명시적 무패턴은 허용하지만 사유는 ScanResult schema가 강제한다.
        log_warning(
            _STEP, "no_patterns",
            "table_access_patterns 가 비어 있음 — 결정론 R/W 링킹 불가",
            verdict=verdict.summary,
        )
        return "table_access_patterns 미산출"

    if verdict.ok:
        log_info(_STEP, "patterns_verified", result=verdict.summary)
        return None

    # 검증 실패 패턴을 downstream에 보내면 검증이 경고로 전락한다.
    log_warning(
        _STEP, "patterns_verify_failed",
        "table_access_patterns 검증 실패 — 정규식이 소스/DDL 과 안 맞음",
        verdict=verdict.summary,
    )
    raise ScanCandidateRejected(f"table access rule verification failed: {verdict.summary}")


# ─────────────────────────────────────────────────────────────
# user 메시지 조립
# ─────────────────────────────────────────────────────────────


def _build_user_message(scanner: dict, tables: list[Table]) -> str:
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
        "project_inventory:",
        json.dumps(scanner, ensure_ascii=False, separators=(",", ":")),
        "",
        "available_skills (이름: 1줄 설명):",
        skills_brief,
        "",
        "ddl_tables (이 프로젝트가 접근하는 DB 테이블 — verify_table_regex 해소 기준):",
        ddl_tables,
        "",
        "위 단서로 프로젝트 성격을 식별하고 ScanProposal 을 출력하라.",
    ])


def _list_skills_brief() -> str:
    """``agents/skills/`` 의 SKILL.md 첫 줄만 직렬화해 후보 판단에 제공한다."""
    skills = build_skill_list(FRAMEWORK_SKILLS_DIR, prefix="- ")
    return "(no skills)" if skills in {"NO_SKILLS_DIR", "NO_SKILLS"} else skills
