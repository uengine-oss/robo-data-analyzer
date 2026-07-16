"""잔여 패턴 루프 — step4 결정론 R/W 링킹이 못 푼 테이블 케이스 처리.

per-case 추적(에이전트를 케이스마다 1회 invoke)을 폐기하고 — 잔여 샘플에서
공통 패턴을 찾아 코드가 일괄 적용하는 루프로 대체. 에이전트 호출 횟수가
케이스 수가 아니라 "패턴 종류 수"에 비례한다.

한 라운드:
1. 잔여 샘플(~30) → 에이전트(scan 과 같은 헬퍼 + 잔여 전용 프롬프트)가 공통 패턴 발견.
2. ``verify_table_patterns`` 로 코드 검증 — 통과 못하면 그 패턴 버리고 종료.
3. 통과분을 패턴 집합에 추가 → 잔여 케이스 파일에 재적용 → 잔여·엣지 갱신.

종료 = 잔여 0 / 패턴 못 찾음 / 검증 실패 / 진전 없음 / 반복 상한.
끝내 못 푼 잔여는 정직하게 "미해결"로 돌려준다 (가짜 답 X).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from llm.agents.agent_output import DdlTransform, ResidualPatternResult, ScanResult
from llm.agents.filesystem_tools import make_filesystem_tools
from pipeline.steps.step3_scan_project.scan_verify_tool import make_verify_table_regex_tool
from pipeline.steps.step4_linking.stage1_regex_grep.regex_table_linker import (
    RegexTableLinker,
    verify_table_patterns,
)
from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode, Table
from system.env_catalog import ENV
from llm.client.usage.cost_budget import CostBudget
from llm.client.runners.structured_agent import run_structured_agent
from shared.log.logger import log_info, log_warning

# 로그 component — link_residual_patterns_step 과 동일 키. 루프는 그 step 의
# 구현부라 같은 키로 묶어야 analyzer.log 에서 한 단계 아래로 자연 정렬된다.
_STEP = "step4.link_residual_patterns"

_RESIDUAL_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "llm" / "agents" / "residual_pattern_system.md"
)


@dataclass
class ResidualOutcome:
    """잔여 패턴 루프 결과."""
    edges: list[ReadsWrites] = field(default_factory=list)   # 루프가 추가로 푼 R/W (순-신규)
    residual: list[dict] = field(default_factory=list)       # 끝내 못 푼 잔여 (정직한 미해결)
    rounds_used: int = 0
    patterns_found: int = 0


async def run_residual_loop(
    *,
    unresolved_cases: list[dict],
    scan_result: dict,
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> ResidualOutcome:
    """잔여 테이블 미해결 케이스 → 패턴 발견 루프 → 추가 R/W 엣지 + 최종 잔여."""
    if not unresolved_cases:
        return ResidualOutcome()
    try:
        scan = ScanResult.model_validate(scan_result)
    except Exception as e:  # noqa: BLE001 — 파싱 실패 표면화 목적
        log_warning(
            _STEP, "scan_invalid",
            "scan_result 파싱 실패 — 잔여 패턴 루프 건너뜀 (전체 미해결 유지)",
            error=f"{type(e).__name__}: {e}",
        )
        return ResidualOutcome(residual=list(unresolved_cases))

    # caller 를 못 찾은 케이스(AST 공백)는 정규식으로 못 고침 — 바로 미해결 격리.
    fixable = [c for c in unresolved_cases if c.get("source_id")]
    truly_unresolved = [c for c in unresolved_cases if not c.get("source_id")]
    if not fixable:
        return ResidualOutcome(residual=truly_unresolved)

    residual_files = {c["file"] for c in fixable}

    # 기준선 — 잔여 파일을 결정론(scan) 패턴만으로 스캔. 결정론 단계가 이미 만든 엣지.
    # 루프가 추가한 "순-신규" 엣지를 가려내는 기준 (재적용 결과에서 이 집합을 뺀다).
    baseline = await RegexTableLinker().link(
        project_root=project_root, code_nodes=code_nodes, tables=tables,
        patterns=scan.table_access_patterns, ddl_transform=scan.ddl_transform,
        restrict_to_files=residual_files,
    )
    baseline_edges = set(baseline.edges)

    extra_patterns = []
    ddl_transform = scan.ddl_transform
    edges: list[ReadsWrites] = []
    round_no = 0

    for round_no in range(1, ENV.pipeline.residual_max_rounds + 1):
        if not fixable:
            break
        sample = fixable[: ENV.pipeline.residual_sample_size]
        log_info(_STEP, "round_start", round=round_no, residual=len(fixable), sample=len(sample))

        found = await _find_pattern(
            sample=sample, scan=scan, ddl_transform=ddl_transform,
            project_root=project_root, code_nodes=code_nodes, tables=tables,
            run_dir=run_dir, cost_budget=cost_budget, round_no=round_no,
            stream_emit=stream_emit,
        )
        if not found.table_access_patterns:
            log_info(_STEP, "round_stop", round=round_no,
                     reason=found.no_pattern_reason or "에이전트가 공통 패턴 못 찾음")
            break

        # 코드 검증 — 검증 안 된 정규식은 안 쓴다. 잔여 파일 한정 (전체 재스캔 회피).
        verdict = await verify_table_patterns(
            patterns=found.table_access_patterns, ddl_transform=found.ddl_transform,
            project_root=project_root, code_nodes=code_nodes, tables=tables,
            restrict_to_files=residual_files,
        )
        if not verdict.ok:
            log_warning(_STEP, "pattern_rejected",
                        "잔여 패턴 검증 실패 — 채택 안 함, 루프 종료",
                        round=round_no, verdict=verdict.summary)
            break

        extra_patterns += found.table_access_patterns
        ddl_transform = found.ddl_transform

        # scan + 누적 extra 패턴을 잔여 파일에 재적용 → 엣지·잔여 갱신.
        applied = await RegexTableLinker().link(
            project_root=project_root, code_nodes=code_nodes, tables=tables,
            patterns=scan.table_access_patterns + extra_patterns,
            ddl_transform=ddl_transform, restrict_to_files=residual_files,
        )
        # 순-신규 엣지 = 재적용 결과 - 기준선 (결정론 단계가 이미 만든 엣지 제외).
        edges = [e for e in applied.edges if e not in baseline_edges]
        next_fixable = [u for u in applied.unresolved if u.get("source_id")]

        # 진전 = 케이스 ID 집합 변화로 측정 (카운트 비교는 해소·신규 동률 시 오판).
        prev_ids = {c["case_id"] for c in fixable}
        next_ids = {u["case_id"] for u in next_fixable}
        resolved = prev_ids - next_ids
        new_cases = next_ids - prev_ids
        log_info(
            _STEP, "round_done", round=round_no,
            new_edges=len(edges), residual=len(next_fixable),
            resolved=len(resolved), new=len(new_cases),
        )

        if not resolved and not new_cases:
            # 케이스 집합 변화 0 — 진짜 진전 없음, 종료.
            fixable = next_fixable
            log_info(_STEP, "round_stop", round=round_no, reason="진전 없음 (케이스 변화 0)")
            break
        fixable = next_fixable

    truly_unresolved += fixable
    return ResidualOutcome(
        edges=edges, residual=truly_unresolved,
        rounds_used=round_no, patterns_found=len(extra_patterns),
    )


async def _find_pattern(
    *,
    sample: list[dict],
    scan: ScanResult,
    ddl_transform: DdlTransform,
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
    run_dir: Path,
    cost_budget: CostBudget,
    round_no: int,
    stream_emit: Optional[Callable[[dict], None]],
) -> ResidualPatternResult:
    """잔여 샘플 1배치 → 에이전트가 공통 패턴 발견.

    scan 과 같은 에이전트 헬퍼(``run_structured_agent``)를 쓰되 — 잔여 전용
    프롬프트로 재탐색·skill 매칭을 막는다. 도구는 코드 열람 + 정규식 검증만
    (skill 도구·graph 도구 없음 — 잔여 모드엔 불필요).
    """
    tools = [
        *make_filesystem_tools(project_root).values(),
        make_verify_table_regex_tool(project_root, code_nodes, tables),
    ]
    return await run_structured_agent(
        agent_name="residual",
        invoke_id=f"round{round_no}",
        system_prompt=_RESIDUAL_PROMPT_PATH.read_text(encoding="utf-8"),
        tools=tools,
        response_format=ResidualPatternResult,
        user_message=_build_residual_message(sample, scan, ddl_transform, tables),
        max_calls=ENV.agent.residual_max_calls,
        run_dir=run_dir,
        cost_budget=cost_budget,
        stream_emit=stream_emit,
    )


def _build_residual_message(
    sample: list[dict], scan: ScanResult, ddl_transform: DdlTransform, tables: list[Table],
) -> str:
    """잔여 샘플 + 이미 파악된 scan 맥락을 user 메시지로 직렬화 (재탐색 금지).

    KV 캐시 — 라운드 사이 prefix 적중 최대화 위해 [라운드 동일 영역] → [라운드 변동
    영역] 순으로 배치. matched_skill / codebase_notes / framework_runtime_functions /
    ddl_tables 는 라운드 사이 동일 → 앞. confirmed_patterns / current_ddl_transform /
    residual_cases 는 라운드마다 변경 → 뒤.
    """
    ddl_tables = ", ".join(sorted(t.name for t in tables)) if tables else "(DDL 없음)"
    confirmed = [p.call_regex for p in scan.table_access_patterns]
    return "\n".join([
        # ─── 라운드 사이 동일 (KV 캐시 적중 영역) ───
        f"matched_skill: {scan.matched_skill or '(없음)'}",
        "",
        "codebase_notes:",
        scan.codebase_notes or "(없음)",
        "",
        f"framework_runtime_functions: {', '.join(scan.framework_runtime_functions) or '(없음)'}",
        "",
        "ddl_tables (verify_table_regex 해소 기준):",
        ddl_tables,
        "",
        # ─── 라운드 변동 (캐시 미스 시작) ───
        "confirmed_patterns (결정론 단계가 이미 쓰는 정규식 — 이것과 겹치지 않는 새 패턴 필요):",
        json.dumps(confirmed, ensure_ascii=False, indent=2),
        "",
        "current_ddl_transform (entity→DDL 변환 — 잔여가 안 풀리면 이 규칙 교정도 고려):",
        json.dumps(ddl_transform.model_dump(), ensure_ascii=False),
        "",
        f"residual_cases ({len(sample)}건 — 이들의 공통 접근 패턴을 찾아라):",
        json.dumps(sample, ensure_ascii=False, indent=2),
        "",
        "위 잔여의 공통 접근 패턴을 찾아 ResidualPatternResult 로 출력하라.",
    ])
