"""잔여 패턴 처리 — step5 결정론 R/W 링킹이 못 푼 테이블 케이스 처리.

matched SKILL의 access/exclusion/DDL 계약은 문서 소유의 폐쇄 계약이므로 runtime에서
확장하지 않고 잔여를 그대로 보존한다. unknown/custom 경로만 잔여 샘플에서 공통 패턴을
strict schema로 한 번 제안받아 코드가 일괄 검증·적용한다.

한 라운드:
1. 잔여 샘플(~30) → 단일 strict-schema LLM 호출이 공통 패턴/DDL 변환을 제안.
2. ``verify_table_patterns`` 로 코드 검증 — 통과 못하면 내용 오류로 중단.
3. 통과분을 패턴 집합에 추가 → 잔여 케이스 파일에 재적용 → 잔여·엣지 갱신.

종료 = 잔여 0 / 명시적 무패턴 / 진전 없음 / 반복 상한.
끝내 못 푼 잔여는 정직하게 "미해결"로 돌려준다 (가짜 답 X).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agents import AGENTS_PACKAGE_DIR
from agents.contracts import (
    DdlTransform,
    ResidualPatternResult,
    ScanResult,
    TableAccessPattern,
)
from agents.rule_compiler import compile_exclusion_rules
from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    RegexTableLinker,
    verify_table_patterns,
)
from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode, Table
from shared.config.settings import SETTINGS
from llm.usage.cost_budget import CostBudget
from llm.runners.yaml_llm_runner import run_yaml_rule
from shared.files.cache import SourceFileCache
from shared.events.runtime_logger import log_info, log_warning

# 로그 component — link_residual_patterns_step 과 동일 키. 루프는 그 step 의
# 구현부라 같은 키로 묶어야 analyzer.log 에서 한 단계 아래로 자연 정렬된다.
_STEP = "step5.link_residual_patterns"

_RESIDUAL_PROMPTS_DIR = AGENTS_PACKAGE_DIR / "prompts"
_RESIDUAL_RULE = "residual"


class ResidualPatternVerificationError(RuntimeError):
    """LLM이 제안한 residual 규칙이 production 검증을 통과하지 못함."""


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
    scan_result: ScanResult,
    project_root: Path,
    code_nodes: list[CodeNode],
    tables: list[Table],
    cost_budget: CostBudget,
) -> ResidualOutcome:
    """잔여 테이블 미해결 케이스 → 패턴 발견 루프 → 추가 R/W 엣지 + 최종 잔여."""
    if not unresolved_cases:
        return ResidualOutcome()
    scan = scan_result

    if scan.matched_skill:
        log_info(
            _STEP,
            "matched_skill_contract_closed",
            skill=scan.matched_skill,
            residual=len(unresolved_cases),
            llm_calls=0,
            reason="documented access/exclusion/DDL contract is authoritative",
        )
        return ResidualOutcome(residual=list(unresolved_cases))

    # caller 를 못 찾은 케이스(AST 공백)는 정규식으로 못 고침 — 바로 미해결 격리.
    fixable = [c for c in unresolved_cases if c.get("source_id")]
    truly_unresolved = [c for c in unresolved_cases if not c.get("source_id")]
    if not fixable:
        return ResidualOutcome(residual=truly_unresolved)

    residual_files = {c["file"] for c in fixable}
    exclusion_patterns = compile_exclusion_rules(scan.table_access_exclusion_rules)

    # 기준선 — 잔여 파일을 결정론(scan) 패턴만으로 스캔. 결정론 단계가 이미 만든 엣지.
    # 루프가 추가한 "순-신규" 엣지를 가려내는 기준 (재적용 결과에서 이 집합을 뺀다).
    baseline = await RegexTableLinker().link(
        project_root=project_root, code_nodes=code_nodes, tables=tables,
        patterns=scan.table_access_patterns, ddl_transform=scan.ddl_transform,
        restrict_to_files=residual_files,
        exclusion_patterns=exclusion_patterns,
    )
    baseline_edges = set(baseline.edges)

    extra_patterns = []
    ddl_transform = scan.ddl_transform
    edges: list[ReadsWrites] = []
    round_no = 0

    for round_no in range(1, SETTINGS.analysis_pipeline.residual_max_rounds + 1):
        if not fixable:
            break
        sample = fixable[: SETTINGS.analysis_pipeline.residual_sample_size]
        log_info(_STEP, "round_start", round=round_no, residual=len(fixable), sample=len(sample))

        found = await _find_pattern(
            sample=sample, scan=scan, ddl_transform=ddl_transform,
            project_root=project_root, tables=tables,
            cost_budget=cost_budget, round_no=round_no,
        )
        if not found.table_access_patterns and found.ddl_transform_override is None:
            log_info(_STEP, "round_stop", round=round_no,
                     reason=found.no_pattern_reason)
            break

        effective_transform = found.ddl_transform_override or ddl_transform
        if (
            not found.table_access_patterns
            and found.ddl_transform_override == ddl_transform
        ):
            raise ResidualPatternVerificationError(
                f"residual round {round_no} proposed an unchanged DDL transform"
            )

        verification_patterns = found.table_access_patterns or _attempted_patterns(
            sample, [*scan.table_access_patterns, *extra_patterns]
        )
        if not verification_patterns:
            raise ResidualPatternVerificationError(
                f"residual round {round_no} transform proposal has no referenced active pattern"
            )

        # production 검증이 단일 진실이다. 실패 제안을 미해결 정상 결과로 바꾸지 않는다.
        verdict = await verify_table_patterns(
            patterns=verification_patterns, ddl_transform=effective_transform,
            project_root=project_root, code_nodes=code_nodes, tables=tables,
            restrict_to_files=residual_files,
            exclusion_patterns=exclusion_patterns,
        )
        if not verdict.ok:
            log_warning(_STEP, "pattern_rejected",
                        "잔여 패턴 검증 실패 — 후보 격리 필요",
                        round=round_no, verdict=verdict.summary)
            raise ResidualPatternVerificationError(
                f"residual round {round_no} verification failed: {verdict.summary}"
            )

        extra_patterns += found.table_access_patterns
        ddl_transform = effective_transform

        # scan + 누적 extra 패턴을 잔여 파일에 재적용 → 엣지·잔여 갱신.
        applied = await RegexTableLinker().link(
            project_root=project_root, code_nodes=code_nodes, tables=tables,
            patterns=scan.table_access_patterns + extra_patterns,
            ddl_transform=ddl_transform, restrict_to_files=residual_files,
            exclusion_patterns=exclusion_patterns,
        )
        # 순-신규 엣지 = 재적용 결과 - 기준선 (결정론 단계가 이미 만든 엣지 제외).
        edges = [e for e in applied.edges if e not in baseline_edges]
        next_fixable = _remaining_original_cases(
            fixable,
            baseline_resolved_sites=getattr(baseline, "resolved_sites", set()),
            applied_resolved_sites=getattr(applied, "resolved_sites", set()),
            new_edges=edges,
        )
        resolved_count = len(fixable) - len(next_fixable)
        log_info(
            _STEP, "round_done", round=round_no,
            new_edges=len(edges), residual=len(next_fixable),
            resolved=resolved_count, new=0,
        )

        if resolved_count == 0:
            # 재적용 중 생긴 다른 패턴의 미해결 후보는 원래 잔여 모집단이
            # 아니다. 실제 원래 물리 지점이 해소되지 않았다면 종료한다.
            fixable = next_fixable
            log_info(_STEP, "round_stop", round=round_no, reason="진전 없음 (원래 지점 해소 0)")
            break
        fixable = next_fixable

    truly_unresolved += fixable
    return ResidualOutcome(
        edges=edges, residual=truly_unresolved,
        rounds_used=round_no, patterns_found=len(extra_patterns),
    )


def _remaining_original_cases(
    original_cases: list[dict],
    *,
    baseline_resolved_sites: set[tuple[str, int, str]],
    applied_resolved_sites: set[tuple[str, int, str]],
    new_edges: list[ReadsWrites],
) -> list[dict]:
    """원래 잔여 모집단을 유지하고 증거가 생긴 물리 지점만 제거한다.

    전체 패턴 재적용은 다른 pattern ID의 미해결 행을 새로 만들 수 있다.
    그 행으로 원래 큐를 교체하면 잔여 수가 부풀기 때문에 절대 합치지
    않는다. 새 실행은 절대 문자 offset으로 동일 지점을 판별하고, 구형
    입력에는 file/line/source 좌표를 호환 경로로 사용한다.
    """
    newly_resolved_sites = applied_resolved_sites - baseline_resolved_sites
    newly_resolved_lines = {
        (edge.evidence_file, edge.evidence_line, edge.source_id)
        for edge in new_edges
        if edge.evidence_file is not None and edge.evidence_line is not None
    }

    remaining: list[dict] = []
    for case in original_cases:
        offset = case.get("evidence_offset")
        exact_site = (
            str(case.get("file") or ""),
            offset,
            str(case.get("source_id") or ""),
        )
        line_site = (
            str(case.get("file") or ""),
            case.get("line"),
            str(case.get("source_id") or ""),
        )
        resolved = (
            offset is not None and exact_site in newly_resolved_sites
        ) or (
            offset is None and line_site in newly_resolved_lines
        )
        if not resolved:
            remaining.append(case)
    return remaining


async def _find_pattern(
    *,
    sample: list[dict],
    scan: ScanResult,
    ddl_transform: DdlTransform,
    project_root: Path,
    tables: list[Table],
    cost_budget: CostBudget,
    round_no: int,
) -> ResidualPatternResult:
    """잔여 샘플 1배치 → strict schema로 패턴/변환을 한 번 제안한다."""
    values = _residual_prompt_values(
        _samples_with_numbered_context(sample, project_root), scan, ddl_transform, tables,
        round_no=round_no,
    )
    return await run_yaml_rule(
        prompts_dir=_RESIDUAL_PROMPTS_DIR,
        rule_name=_RESIDUAL_RULE,
        user_values=values,
        response_model=ResidualPatternResult,
        budget=cost_budget,
    )


def _residual_prompt_values(
    sample: list[dict],
    scan: ScanResult,
    ddl_transform: DdlTransform,
    tables: list[Table],
    *,
    round_no: int,
) -> dict[str, str]:
    """잔여 샘플과 확정 맥락을 YAML prompt placeholder로 직렬화한다.

    YAML이 고정 영역→변동 영역 순서를 소유하므로 이 함수는 값만 만든다.
    """
    return {
        "id": f"residual-round-{round_no}",
        "matched_skill": scan.matched_skill or "(없음)",
        "framework_runtime_functions": (
            ", ".join(scan.framework_runtime_functions) or "(없음)"
        ),
        "ddl_tables": ", ".join(sorted(t.name for t in tables)) or "(DDL 없음)",
        "confirmed_patterns": json.dumps(
            [p.model_dump() for p in scan.table_access_patterns],
            ensure_ascii=False,
            indent=2,
        ),
        "confirmed_exclusions": json.dumps(
            [rule.model_dump() for rule in scan.table_access_exclusion_rules],
            ensure_ascii=False,
            indent=2,
        ),
        "current_ddl_transform": json.dumps(
            ddl_transform.model_dump(), ensure_ascii=False
        ),
        "residual_cases": json.dumps(
            [_prompt_case(case) for case in sample], ensure_ascii=False, indent=2
        ),
    }


def _attempted_patterns(
    sample: list[dict], active_patterns: list[TableAccessPattern],
) -> list[TableAccessPattern]:
    """변환-only 제안을 실제 잔여를 만든 활성 패턴에만 검증한다."""
    attempted_ids = {
        str(attempt.get("pattern_id") or "")
        for case in sample
        for attempt in (case.get("attempted_patterns") or [])
        if attempt.get("pattern_id")
    }
    return [pattern for pattern in active_patterns if pattern.id in attempted_ids]


def _prompt_case(case: dict) -> dict:
    """반복 정규식 본문을 registry로 올리고 사례별 변동 증거만 남긴다."""
    attempts = [
        {
            "pattern_id": attempt.get("pattern_id"),
            "groups": attempt.get("groups") or {},
            "ddl_lookup_result": attempt.get("ddl_lookup_result"),
        }
        for attempt in (case.get("attempted_patterns") or [])
    ]
    return {
        "case_id": case.get("case_id"),
        "file": case.get("file"),
        "line": case.get("line"),
        "call_text": case.get("call_text"),
        "source_id": case.get("source_id"),
        "attempted_patterns": attempts,
        "source_context_status": case.get("source_context_status"),
        "source_coordinate_system": case.get("source_coordinate_system"),
        "numbered_source_context": case.get("numbered_source_context"),
    }


def _samples_with_numbered_context(
    sample: list[dict],
    project_root: Path,
    *,
    radius: int = 5,
) -> list[dict]:
    """잔여 케이스에 접근 가능한 SOURCE 주변 절대 줄번호를 붙인다."""
    root = project_root.resolve()
    cache = SourceFileCache()
    enriched: list[dict] = []
    for case in sample:
        item = dict(case)
        raw_path = Path(str(case.get("file") or ""))
        path = (raw_path if raw_path.is_absolute() else root / raw_path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            item["source_context_status"] = "outside-project-root"
            item["numbered_source_context"] = ""
            enriched.append(item)
            continue
        line = max(int(case.get("line") or 1), 1)
        excerpt = cache.excerpt(str(path), max(1, line - radius), line + radius)
        item["source_context_status"] = excerpt.status
        item["source_coordinate_system"] = excerpt.coordinate_system
        item["numbered_source_context"] = excerpt.numbered_text
        enriched.append(item)
    return enriched
