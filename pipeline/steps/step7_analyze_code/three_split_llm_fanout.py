"""3 분할 fan-out 헬퍼 — ``analyze_code_step`` 이 코드노드당 1 회 호출.

코드노드 1개당 LLM 호출 (병렬, 2~3 번):
- ``code_summary``        → canonical summary + stereotype + questions (항상)
- ``code_rules_examples`` → branch-coordinate rules (항상 — framework 는 함수 단위)
- ``code_table_insights`` → table_insights (R/W 테이블 있을 때만 — 없으면 skip)

각 concern은 필요한 증거만 받는다. Rule은 행별 직접 증거와 callee summary를 받고,
자식 Rule·question을 부모 입력에 다시 복사하지 않는다.

**KV 캐시 흐름**:
서버 KV cache는 동작하지만 API cached-token 필드는 0만 보고한다. 저장 prompt의 실제
바이트를 대조하면 세 concern은 공통 system prefix만 공유하고 user prompt는 concern별로
갈라진다. 따라서 첫 summary 한 번으로 전역 system prefix를 워밍하되, 이후 코드노드에서
summary를 기다려도 node-specific prefix는 늘지 않는다. 각 노드의 summary/rules/insights는
동시에 시작하고, summary가 끝나는 즉시 caller 의존만 조기 해제한다.

DBMS 부모 range 는 본문이 ``compact_body`` (자식 구간 → 자식 summary 치환) — 자식이
먼저 분석되어 그 결과가 부모 입력으로 합류. Framework 는 평면이라 raw 본문.

각 호출은 Pydantic ``response_format`` 강제 — 자유 텍스트 응답 차단.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline.steps.step7_analyze_code.range_body import (
    ancestor_context,
    body_for,
    raw_body_for,
)
from pipeline.steps.step7_analyze_code.rw_table_filter import (
    serialize_rules,
)
from pipeline.steps.step7_analyze_code.table_prompt_context import dump_table_refs
from pipeline.quality_gates.coverage_residue import coverage_residue
from pipeline.steps.step7_analyze_code.summary_quality import normalize_canonical_summary
from pipeline.models.analysis_context import AnalysisContext, dump_external_evidence, dump_referenced_symbols
from pipeline.models.analysis_finding import AnalysisFinding
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import (
    exclude_contradicted_stereotype,
    require_stereotype,
)
from llm.usage.cost_budget import CostBudget
from pipeline.models.code_analysis_output import (
    RulesExamplesOutput,
    SummaryOutput,
)
from pipeline.models.rule_response_schema import (
    normalize_rules_response,
    rules_response_model,
)
from pipeline.models.summary_response_schema import (
    normalize_summary_response,
    summary_response_model,
)
from pipeline.models.table_insight_response_schema import (
    normalize_table_insights_response,
    table_insights_response_model,
)
from pipeline.steps.step7_analyze_code.rule_row_batching import (
    collect_rules_by_rows,
    plan_batches,
    rule_queue_priority,
    SUMMARY_WAKE_PRIORITY,
)
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from shared.events.runtime_logger import log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore
from pipeline.steps.step7_analyze_code.control_prompt import build_control_inventory
from pipeline.execution.run_output_context import active_run_dir
from pipeline.steps.step7_analyze_code.quality_findings import apply_analysis_findings
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence

_STEP = "step7.analyze_code.three_split"


async def run_summary(
    code_id: str,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    codebase_notes: str = "",
    module_intents: Optional[dict[str, str]] = None,
    table_descriptions: Optional[dict[str, dict]] = None,
    source_cache: Optional[SourceFileCache] = None,
    llm_slots: Optional[PrioritySemaphore] = None,
) -> SummaryOutput | ModelOutputError:
    """#1 summary 단독 호출 — Round 첫 코드노드의 cache 워밍용.

    ``analyze_code_step`` 가 Round 시작 시 첫 코드노드에 대해 호출 → cache 에
    [공통지침 + 첫 코드노드 입력값] 올림. 결과를 ``precomputed_summary`` 로 첫 코드노드의
    ``analyze_code_three_split`` 에 다시 전달해 #1 중복 호출 방지.

    track_processing 이 start/done/failed 자동 emit (stream 인자 chain X).

    실패는 runner가 원인·prompt를 기록한 뒤 그대로 전파한다.
    """
    if llm_slots is None:
        raise ValueError("run_summary requires the run-scoped LLM semaphore")
    ctx = contexts.get(code_id)
    fn = code_by_id.get(code_id)
    if ctx is None or fn is None:
        log_warning(
            _STEP, "warmup_input_missing",
            "워밍 요약 입력 부재 — 이 Round 는 캐시 프리픽스 워밍 없이 진행한다.",
            code_id=code_id, ctx_missing=ctx is None, node_missing=fn is None,
        )
        raise KeyError(
            f"summary 입력 누락: code_id={code_id!r}, "
            f"ctx_missing={ctx is None}, node_missing={fn is None}"
        )

    common, _inventory = _build_user_values(
        ctx, fn, contexts, codebase_notes,
        module_intents or {}, table_descriptions or {},
        source_cache or SourceFileCache(),
    )
    async def _call() -> SummaryOutput:
        async with track_processing("CodeNode", code_id, sub_action="warmup_summary"):
            async with llm_slots:
                output = await run_yaml_rule(
                    prompts_dir=Path(strategy.prompts_dir),
                    rule_name="code_summary",
                    user_values=common,
                    response_model=summary_response_model(strategy.name),
                    budget=cost_budget,
                )
                return normalize_summary_response(output)

    try:
        return await _call()
    except ModelOutputError as error:
        return error


async def analyze_code_three_split(
    code_id: str,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    codebase_notes: str = "",
    module_intents: Optional[dict[str, str]] = None,
    table_descriptions: Optional[dict[str, dict]] = None,
    source_cache: Optional[SourceFileCache] = None,
    precomputed_summary: Optional[SummaryOutput | ModelOutputError] = None,
    precomputed_summary_supplied: bool = False,
    on_summary_ready: Optional[Callable[[str], None]] = None,
    llm_slots: Optional[PrioritySemaphore] = None,
) -> CodeNode:
    """코드노드 1개의 3 분할 분석 — 결과를 ``CodeNode`` 에 적용해 반환.

    흐름: #1 summary + #2 rules + #3 insights 동시. ``precomputed_summary`` 가 있으면
    #1만 재호출하지 않는다. 확정된 callee summary는 공통 입력에 남지만 현재 노드
    summary 결과를 rules에 다시 주입하지 않아 concern 간 순환 의존을 만들지 않는다.

    Args:
        codebase_notes: ``ScanResult.codebase_notes`` — prefix 공유로 캐시 적중.
        module_intents: ``{module_id: intent}`` — module_pre_analysis(M9) 결과.
        table_descriptions: ``{table_fqn: DDL 설명}`` — reads/writes 렌더에 의미 부여.
        precomputed_summary: Round 첫 코드노드의 워밍 결과.
        precomputed_summary_supplied: 워밍 호출이 이미 끝났는지 표시. 워밍 실패(None)도
            여기서 다시 호출하지 않아 concern 최대 2회 계약을 지킨다.
        on_summary_ready: summary 확정(경계 검사 후) 직후 호출되는 스케줄러 신호 —
            CALLS 의존 caller 를 #2/#3 완료를 기다리지 않고 조기 투입하게 한다.

    Returns:
        검증된 관심사는 보존하고 모델 내용/schema 실패 관심사는 finding으로 격리한 CodeNode.
    """
    if llm_slots is None:
        raise ValueError("analyze_code_three_split requires the run-scoped LLM semaphore")
    ctx = contexts.get(code_id)
    fn = code_by_id.get(code_id)
    if ctx is None or fn is None:
        # 조용히 None 을 내면 호출부가 analyzed/failed 어디에도 안 세어 노드가 실종된다
        # (헌법 IV / spec 045 FR-008) → 원인(둘 중 뭐가 없는지)을 남긴다.
        log_warning(
            _STEP, "analysis_input_missing",
            "코드노드 분석 입력 부재 — 이 노드는 summary 없이 남는다(검색 코퍼스 제외).",
            code_id=code_id, ctx_missing=ctx is None, node_missing=fn is None,
        )
        raise KeyError(
            f"analysis 입력 누락: code_id={code_id!r}, "
            f"ctx_missing={ctx is None}, node_missing={fn is None}"
        )

    # 대상 사실의 한 정의처. 각 concern template은 여기서 자기 책임 필드만 선택하며 Rule은
    # raw SOURCE 대신 compact 행 evidence를 사용한다. inventory는 배칭·채점의 구조 정답지다.
    common, inventory = _build_user_values(
        ctx, fn, contexts, codebase_notes,
        module_intents or {}, table_descriptions or {},
        source_cache or SourceFileCache(),
    )
    rules_values = common
    prompts_dir = Path(strategy.prompts_dir)

    # split 단위 wrapper — code_id 캡처 closure (응집 ★).
    async def _track_split(sub_action: str, invoke, *, priority: int = 0):
        async with track_processing("CodeNode", code_id, sub_action=sub_action):
            async with llm_slots.at(priority):
                return await invoke()

    # Server logs confirm KV caching, but recorded prompt bytes show that summary/rules
    # share only the global system prompt; their user prompts diverge after 3 chars.
    # The one global warmup remains, while per-node concerns can start independently.
    async def _collect_summary():
        if precomputed_summary_supplied:
            summary_out = precomputed_summary
            if summary_out is None:
                raise RuntimeError(f"precomputed summary missing: {code_id}")
        else:
            try:
                dynamic_summary = await _track_split(
                    "split_summary",
                    lambda: run_yaml_rule(
                        prompts_dir=prompts_dir, rule_name="code_summary",
                        user_values=common,
                        response_model=summary_response_model(strategy.name),
                        budget=cost_budget,
                    ),
                    priority=SUMMARY_WAKE_PRIORITY,
                )
                summary_out = normalize_summary_response(dynamic_summary)
            except ModelOutputError as error:
                summary_out = error

        findings: list[AnalysisFinding] = []
        if isinstance(summary_out, ModelOutputError):
            fn.extra["questions"] = []
            findings.append(AnalysisFinding(
                concern="summary",
                status="unavailable",
                target_id=code_id,
                reasons=(str(summary_out),),
                evidence_rule=summary_out.rule_name,
            ))
        else:
            summary_out.summary = normalize_canonical_summary(summary_out.summary)
            contradiction = _apply_summary_fields(fn, ctx, summary_out, strategy)
            if contradiction is not None:
                findings.append(contradiction)
            ctx.summary = summary_out.summary
        if on_summary_ready is not None:
            on_summary_ready(code_id)
        return findings

    def _rule_values(rows):
        evidence = rule_evidence(inventory, rows)
        evidence_json = json.dumps(
            evidence, ensure_ascii=False, separators=(",", ":"),
        )
        values = {
            **rules_values,
            "assigned_rows": ", ".join(map(str, rows)),
            "rule_evidence": evidence_json,
        }
        return evidence, evidence_json, values

    async def _invoke_rules(rows):
        evidence, _evidence_json, values = _rule_values(rows)
        output = await _track_split(
            "split_rules",
            lambda: run_yaml_rule(
                prompts_dir=prompts_dir, rule_name="code_rules_examples",
                user_values=values,
                response_model=rules_response_model(rows),
                budget=cost_budget,
            ),
            priority=rule_queue_priority(len(_evidence_json)),
        )
        return normalize_rules_response(
            output,
            rows,
            evidence.get("conditions", {}),
            evidence.get("effects", {}),
        )

    async def _collect_rules():
        return await collect_rules_by_rows(
            invoke=_invoke_rules,
            inventory=inventory,
            target_scope=code_id,
            source=common["raw_code_body"],
            # 주입한 근거(callee 반환 표현식·API 계약값)를 창작 검사 풀에
            # 합류 — 모델에게 준 증거를 창작으로 오탐하지 않게 (spec 116 §19).
            extra_values=sorted(_grounded_external_values(ctx, contexts)),
            allowed_tables=sorted(set(ctx.reads) | set(ctx.writes)),
            read_tables=sorted(set(ctx.reads)),
            write_tables=sorted(set(ctx.writes)),
            table_ops=ctx.extra.get("table_op") or {},
        )

    gather_map: dict[str, Any] = {
        "summary": _collect_summary(),
        "rules": _collect_rules(),
    }
    if ctx.reads or ctx.writes:
        insight_model = table_insights_response_model(
            ctx.reads, ctx.writes, ctx.extra.get("table_op") or {},
        )
        async def _collect_insights():
            try:
                output = await _track_split(
                    "split_insights",
                    lambda: run_yaml_rule(
                        prompts_dir=prompts_dir, rule_name="code_table_insights",
                        user_values=common, response_model=insight_model, budget=cost_budget,
                    ),
                )
                return output, None
            except ModelOutputError as error:
                return None, error

        gather_map["insights"] = _collect_insights()

    gather_tasks = {
        name: asyncio.create_task(coroutine)
        for name, coroutine in gather_map.items()
    }
    try:
        results = await asyncio.gather(*gather_tasks.values())
    except BaseException:
        # One internal invariant must not leave sibling model calls running after the
        # node itself has failed or the run was cancelled.
        for task in gather_tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*gather_tasks.values(), return_exceptions=True)
        raise
    done = dict(zip(gather_tasks, results))
    quality_findings: list[AnalysisFinding] = done["summary"]
    collection = done.get("rules")
    insights_result = done.get("insights")
    insights_out = insights_result[0] if insights_result is not None else None
    if insights_result is not None and insights_result[1] is not None:
        error = insights_result[1]
        quality_findings.append(AnalysisFinding(
            concern="table_insights",
            status="unavailable",
            target_id=code_id,
            reasons=(str(error),),
            evidence_rule=error.rule_name,
        ))
    if insights_out is not None:
        insights_out = normalize_table_insights_response(
            insights_out, ctx.reads, ctx.writes,
        )

    obligation_rows = sum(len(batch) for batch in plan_batches(inventory))

    # fan-in 머지 → CodeNode 에 적용 (#1 의미 + 결정론 검색 필드는 이미 확정됐다).
    # 자연어 규칙 시절의 품질·근거성 사후 관측은 은퇴 — 결정론 채점(grade_rules)이
    # 생성 시점에 행 단위로 대체했다(spec 116).
    fn.extra["table_insights"] = (
        [t.model_dump() for t in insights_out.table_insights] if insights_out else []
    )
    serialized, unresolved_tables = serialize_rules(
        collection.rules, fn.signature or fn.id, ctx.reads, ctx.writes,
        ctx.extra.get("table_op"),
    )
    fn.extra["rules"] = serialized
    quality_findings.extend(collection.findings)
    finding_records = [finding.to_dict() for finding in quality_findings]
    apply_analysis_findings(fn, quality_findings, component=_STEP)
    fn.extra["rule_grading"] = {
        "obligation_rows": obligation_rows,
        "accepted_rows": [rule.anchor_line for rule in collection.rules],
        "finding_rows": sorted({
            row for item in finding_records for row in item["coordinates"]
        }),
        "calls": collection.calls,
    }
    if unresolved_tables:
        raise ValueError(
            f"{code_id}: then 테이블 효과가 결정론 R/W와 불일치: "
            f"{sorted(set(unresolved_tables))[:8]}"
        )
    # ctx 에 나머지 분석 결과 부착 — 부모(PARENT_OF)의 #3 분할 입력.
    # (summary 는 자기 concern 완료 시 즉시 공개됨 — CALLS 의존 조기 해제.)
    ctx.extra["rules"] = fn.extra["rules"]
    ctx.extra["questions"] = fn.extra["questions"]
    return fn


def _apply_summary_fields(
    fn: CodeNode,
    ctx: AnalysisContext,
    summary_out: SummaryOutput,
    strategy: Any,
) -> AnalysisFinding | None:
    """#1 summary 에서만 나오는 코드노드 필드를 확정한다 (spec 096).

    #2(insights)·#3(rules)는 이 의미/검색 필드를 읽지도 쓰지도 않는다. 그래서 노드 완료가
    아니라 #1 확정 시점에 넣어도 값이 같고, 후처리는 그만큼 일찍 시작할 수 있다.
    stereotype 은 ontology_roles.yaml 풀과 검증 (없으면 빈 문자열로 강제).
    """
    stereotype, violation = exclude_contradicted_stereotype(
        require_stereotype(summary_out.stereotype, strategy.name),
        has_writes=bool(ctx.writes),
    )
    if violation:
        log_warning(
            _STEP, "stereotype_contradiction",
            "stereotype 이 결정론 쓰기 근거와 모순 — 빈 값으로 강제(사람 검토)",
            code_id=fn.id, reason=violation,
        )
    fn.summary = summary_out.summary
    fn.stereotype = stereotype or None
    fn.extra["questions"] = [q.model_dump() for q in summary_out.questions]
    if not violation:
        return None
    return AnalysisFinding(
        concern="stereotype",
        status="rejected",
        target_id=fn.id,
        reasons=(violation,),
        candidate={"stereotype": summary_out.stereotype},
        evidence_rule="code_summary",
    )


def _build_user_values(
    ctx: AnalysisContext,
    fn: CodeNode,
    contexts: dict[str, AnalysisContext],
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, dict],
    cache: SourceFileCache,
) -> tuple[dict, object]:
    """3 yaml 공통 ``user_template`` 의 치환 값 ([공통 context]) + 분기 대장.

    DBMS 부모 range 는 ``code_body`` 가 compact (자식 구간 → 자식 summary 치환).
    ``ancestor_context`` 는 조상 range 골격 (Framework 는 평면이라 "(없음)").
    ``reads`` / ``writes`` 는 테이블 fqn + DDL 설명 — LLM 이 테이블 의미를 알고 분석.

    분기 대장(BranchInventory)은 프롬프트 branch_index 와 행 채점·배칭이 **같은
    정의처**를 공유해야 하므로 여기서 1회 만들어 함께 돌려준다 (spec 116).
    """
    excerpt = cache.excerpt(ctx.file_path, ctx.start_line, ctx.end_line)
    raw_body = raw_body_for(ctx, contexts, cache)
    prompt_body = body_for(ctx, contexts, cache)
    inventory = build_control_inventory(fn, ctx, cache)
    # 상태 추적 없이 소스에 직접 쓰인 반환 표현식만 bottom-up 인터페이스로 전달한다.
    ctx.extra["return_expressions"] = sorted(inventory.return_expressions)
    _persist_branch_table(fn, inventory)
    _persist_coverage(fn, inventory)
    values = {
        "id": fn.id,
        "signature": fn.signature,
        "code_body": prompt_body,
        "raw_code_body": raw_body,
        "numbered_raw_code_body": excerpt.numbered_text,
        "source_file": fn.file_path,
        "source_range": f"{ctx.start_line}-{ctx.end_line}",
        "source_status": excerpt.status,
        "coordinate_system": excerpt.coordinate_system,
        "codebase_notes": codebase_notes or "(없음)",
        "module_intent": module_intents.get(fn.owner_id) or "(없음)",
        "referenced_symbols": dump_referenced_symbols(ctx.referenced_symbols),
        "external_evidence": dump_external_evidence(ctx.external_evidence),
        "callee_summaries": _format_callee_summaries(ctx, contexts),
        "ancestor_context": ancestor_context(ctx, contexts, cache),
        "reads": dump_table_refs(ctx.reads, table_descriptions, include_columns=False),
        "writes": dump_table_refs(ctx.writes, table_descriptions, include_columns=False),
        "table_ops": json.dumps(
            ctx.extra.get("table_op") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        # spec 102: 규칙이 고를 수 있는 근거 분기 후보. 분할 3 템플릿만 참조한다.
        "branch_index": inventory.branch_index(),
        # spec 116 §19: SKILL 문서 보증 API 값 계약 — 분할 3 템플릿만 참조한다.
        "api_contracts": (
            json.dumps(
                [dict(contract) for contract in ctx.extra["api_contracts"]],
                ensure_ascii=False, separators=(",", ":"),
            )
            if ctx.extra.get("api_contracts") else "(없음)"
        ),
    }
    return values, inventory


def _persist_branch_table(fn: CodeNode, inventory) -> None:
    """활성 분석 run에만 분기표 정답지를 기록한다.

    독립 단위 호출에는 run 산출물 소유권이 없으므로 기본 ``logs/``로 fallback하지 않는다.
    """
    run_dir = active_run_dir()
    if run_dir is None:
        return
    directory = run_dir / "branch-tables"
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in fn.id
    )
    (directory / f"{safe_name}.json").write_text(
        json.dumps(inventory.table_rows(), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _persist_coverage(fn: CodeNode, inventory) -> None:
    """커버리지 잔차 감사(골키퍼 그물2)를 run 산출물로 기록한다.

    함수별 잔차·위험구문 장부는 `scripts/audit_coverage.py` 가 corpus 단위로 집계한다.
    분기표와 같은 소유권 규칙 — 활성 run 이 없으면 기록하지 않는다.
    """
    run_dir = active_run_dir()
    if run_dir is None:
        return
    directory = run_dir / "coverage"
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in fn.id
    )
    report = coverage_residue(inventory)
    report["function"] = fn.id
    (directory / f"{safe_name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _callee_interface_items(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> list[dict]:
    """Canonical callee summaries used by display/search/bottom-up exactly once.

    Bottom-up DAG 진행 중이면 callee summary가 ``contexts``에 있음.
    아직 없으면(cycle 내부·분석 실패) 다른 의미 필드로 대신하지 않고 목록에서 제외한다.
    """
    items: list[dict] = []
    for callee_id in ctx.callees:
        c = contexts.get(callee_id)
        if c is None:
            continue
        text = (c.summary or "").strip()
        if text:
            items.append({"callee_id": callee_id, "summary": text})
    return items


def _grounded_external_values(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> frozenset[str]:
    """프롬프트의 외부 근거 값 집합 — 채점기 창작 검사 풀 확장분.

    렌더(_callee_interface_items·api_contracts)와 같은 데이터에서 뽑는다. 필드 원문
    (F() 검사)은 여기 넣지 않는다 — callee 동작 복사 금지 계약은 채점기가 계속 강제.
    """
    facts: set[str] = set()

    def add(value: str) -> None:
        facts.add(value)
        # C 원문 인용은 따옴표째 온다(`"00"`) — 모델은 값 글자("00")로 쓰므로
        # 짝이 맞는 인용부호를 벗긴 표기도 같은 증거로 인정한다.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            facts.add(value[1:-1])

    for contract in ctx.extra.get("api_contracts") or ():
        success_value = contract.get("success_value")
        if success_value:
            add(success_value)
    return frozenset(facts)


def _format_callee_summaries(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> str:
    """Render each available callee canonical summary exactly once."""
    if not ctx.callees:
        return "(없음)"
    items = _callee_interface_items(ctx, contexts)
    return (
        json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        if items else "(없음)"
    )
