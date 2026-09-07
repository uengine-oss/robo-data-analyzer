"""관심사 fan-out 헬퍼 — ``analyze_code_step`` 이 코드노드당 1회 호출.

코드노드 1개당 서로 다른 LLM 관심사(병렬, 정상 경로 1~2회):
- ``code_summary`` → canonical summary + stereotype (항상)
- ``code_rule_meanings`` → Analyzer가 지정한 condition/effect atom 의미와 Rule 여부
  (Rule row와 atom이 있을 때만 — 없으면 skip)

Rule의 구조와 좌표는 parser-owned branch evidence에서 결정론으로 만들고, 모델은 배정된 atom의
업무 의미만 채우며 파생 화면 데이터를 별도로 생성하지 않는다.
각 관심사는 자기 근거만 한 번 받는다.

**KV 캐시 흐름**:
서버 KV cache는 동작하지만 API cached-token 필드는 0만 보고한다. 저장 prompt의 실제
바이트를 대조하면 두 LLM concern은 하나의 system prompt를 공유하고 user prompt는 concern별로
갈라진다. 따라서 첫 summary 한 번으로 전역 system prefix를 워밍하되, 이후 코드노드에서
summary를 기다려도 node-specific prefix는 늘지 않는다. summary/rule meaning은 독립 실행하고
summary가 끝나는 즉시 caller 의존만 조기 해제한다.

Framework와 DBMS 모두 현재 TARGET의 정확한 원문을 사용한다. 자식 원문을 생성 요약으로
치환하지 않으며, 필요한 direct callee 결과만 별도 구조화 문맥으로 한 번 전달한다.

각 호출은 Pydantic ``response_format`` 강제 — 자유 텍스트 응답 차단.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from pipeline.meaning.source_body import exact_source_body
from pipeline.meaning.source_outline import render_source_outline
from pipeline.meaning.table.rule_tables import (
    serialize_rules,
)
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.analysis_finding import AnalysisFinding
from graph.schema.graph_nodes import CodeNode
from llm.usage.cost_budget import CostBudget
from pipeline.models.code_analysis_output import (
    SummaryOutput,
)
from pipeline.meaning.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.meaning.table.table_usage_projection import project_direct_table_usages
from pipeline.checks.rule_obligations import required_rule_rows
from pipeline.meaning.rule.row_batching import (
    materialize_and_validate_rules,
)
from llm.runners.model_output_error import ModelOutputError
from shared.events.runtime_logger import log_warning
from shared.files.source_cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore
from pipeline.execution.semantic_artifacts import persist_function_semantic_frame
from pipeline.meaning.rule.control_inventory import build_control_inventory
from pipeline.meaning.rule.branch_audit import (
    persist_branch_table,
    persist_coverage,
)
from pipeline.checks.rule_audit_artifact import persist_rule_audit
from pipeline.meaning.summary.function_meaning import finalize_function_meaning
from pipeline.meaning.quality_findings import record_code_analysis_findings
from pipeline.meaning.meaning_requests import (
    MeaningRequestRunner,
    direct_callee_effects_by_id,
)
from pipeline.meaning.summary_results import apply_summary_result

_STEP = "step7.analyze_code.meanings"


def _project_referenced_symbols(ctx: AnalysisContext) -> list[dict[str, object]]:
    """Keep only source-external symbol facts that can change code meaning."""
    projected: list[dict[str, object]] = []
    for symbol in ctx.referenced_symbols:
        value: dict[str, object] = {"name": symbol.name, "kind": symbol.kind}
        for key, item in (
            ("type", symbol.data_type),
            ("value", symbol.declared_value),
            ("value_meaning", symbol.value_semantics),
        ):
            if str(item).strip():
                value[key] = item
        if symbol.initialization_sources:
            value["initialization_sources"] = list(symbol.initialization_sources)
        if len(value) > 2:
            projected.append(value)
    return projected


async def analyze_code_meanings(
    code_id: str,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    strategy: Any,
    cost_budget: Optional[CostBudget] = None,
    table_descriptions: Optional[dict[str, dict]] = None,
    source_cache: Optional[SourceFileCache] = None,
    precomputed_summary: Optional[SummaryOutput | ModelOutputError] = None,
    precomputed_summary_supplied: bool = False,
    summary_warm_event: asyncio.Event | None = None,
    is_summary_warmup: bool = False,
    on_summary_ready: Optional[Callable[[str], None]] = None,
    llm_slots: Optional[PrioritySemaphore] = None,
) -> CodeNode:
    """코드노드 1개의 관심사 분석 결과를 ``CodeNode``에 적용해 반환.

    흐름: Summary와 Rule meaning LLM을 독립 실행한다.
    ``precomputed_summary``가 있으면 summary만 재호출하지 않는다. Summary와 Rule에는
    각 concern이 실제로 소비하는 결정론적 callee 효과만 한 번 전달한다.

    Args:
        table_descriptions: ``{table_fqn: DDL 설명}`` — reads/writes 렌더에 의미 부여.
        precomputed_summary: 테스트나 명시적 호출자가 이미 얻은 Summary 결과.
        precomputed_summary_supplied: 워밍 호출이 이미 끝났는지 표시. 워밍 실패(None)도
            여기서 같은 summary를 다시 호출하지 않아 exact-once 관심사 계약을 지킨다.
        summary_warm_event: 첫 Summary 완료 뒤 다른 Summary를 시작시키는 run-scoped 신호.
            Rule concern은 이 신호를 기다리지 않는다.
        is_summary_warmup: 이 노드의 Summary가 신호를 여는 첫 호출인지 표시.
        on_summary_ready: summary 확정(경계 검사 후) 직후 호출되는 스케줄러 신호 —
            caller를 Rule concern 완료를 기다리지 않고 조기 투입하게 한다.

    Returns:
        검증된 관심사는 보존하고 모델 내용/schema 실패 관심사는 finding으로 격리한 CodeNode.
    """
    if llm_slots is None:
        raise ValueError("analyze_code_meanings requires the run-scoped LLM semaphore")
    ctx = contexts.get(code_id)
    fn = code_by_id.get(code_id)
    if ctx is None or fn is None:
        # 조용히 None 을 내면 호출부가 analyzed/failed 어디에도 안 세어 노드가 실종된다
        # 헌법 IV에 따라 원인(둘 중 무엇이 없는지)을 남긴다.
        log_warning(
            _STEP, "analysis_input_missing",
            "코드노드 분석 입력 부재 — 이 노드는 summary 없이 남는다(검색 코퍼스 제외).",
            code_id=code_id, ctx_missing=ctx is None, node_missing=fn is None,
        )
        raise KeyError(
            f"analysis 입력 누락: code_id={code_id!r}, "
            f"ctx_missing={ctx is None}, node_missing={fn is None}"
        )

    # 대상 사실의 한 정의처. 각 LLM concern template은 자기 책임 필드만 선택하고,
    # inventory는 Rule 물질화·감사의 구조 정답지다.
    analysis_inputs, inventory = _prepare_analysis_inputs(
        ctx,
        fn,
        contexts,
        code_by_id,
        source_cache or SourceFileCache(),
        table_descriptions or {},
    )
    requests = MeaningRequestRunner(
        code_id=code_id,
        strategy_name=strategy.name,
        llm_slots=llm_slots,
        cost_budget=cost_budget,
    )

    # Server logs confirm KV caching, but recorded prompt bytes show that the three
    # concerns share one canonical system prompt; their user prompts diverge immediately.
    # The one global warmup remains, while per-node concerns can start independently.
    async def _collect_summary():
        if precomputed_summary_supplied:
            summary_out = precomputed_summary
            if summary_out is None:
                raise RuntimeError(f"precomputed summary missing: {code_id}")
        else:
            if summary_warm_event is not None and not is_summary_warmup:
                await summary_warm_event.wait()
            try:
                profile, _all_specs, concerns = ctx.extra["_semantic_frame_plan"]
                slot_specs = concerns["summary"]
                summary_out = await requests.summary(
                    profile=profile,
                    slot_specs=slot_specs,
                    analysis_inputs=analysis_inputs,
                )
            except ModelOutputError as error:
                summary_out = error

        if is_summary_warmup and summary_warm_event is not None:
            summary_warm_event.set()

        findings = apply_summary_result(
            fn=fn,
            ctx=ctx,
            contexts=contexts,
            analysis_inputs=analysis_inputs,
            strategy=strategy,
            result=summary_out,
        )
        if on_summary_ready is not None:
            on_summary_ready(code_id)
        return findings

    collection = materialize_and_validate_rules(
        inventory=inventory,
        target_scope=code_id,
        source=analysis_inputs["raw_source_code"],
        allowed_tables=sorted(set(ctx.reads) | set(ctx.writes)),
        read_tables=sorted(set(ctx.reads)),
        write_tables=sorted(set(ctx.writes)),
        table_ops=ctx.extra.get("table_op") or {},
        table_aliases=ctx.extra.get("table_identity_aliases") or {},
        condition_bundle=fn.extra.get("_resolved_rule_condition_bundle"),
    )

    async def _collect_rule_meanings():
        return await requests.rules(
            collection=collection,
            ctx=ctx,
            contexts=contexts,
            table_descriptions=table_descriptions or {},
        )

    gather_map: dict[str, Any] = {
        "summary": _collect_summary(),
        "rules": _collect_rule_meanings(),
    }

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
    collection = done["rules"]

    obligation_rows = len(required_rule_rows(inventory))

    # fan-in 머지 → CodeNode 에 적용 (#1 의미 + 결정론 검색 필드는 이미 확정됐다).
    # 자연어 규칙 시절의 품질·근거성 사후 관측은 은퇴 — 결정론 채점(grade_rules)이
    # 생성 시점에 행 단위로 대체했다.
    serialized, unresolved_tables = serialize_rules(
        collection.rules, ctx.reads, ctx.writes,
        ctx.extra.get("table_op"), ctx.extra.get("table_identity_aliases") or {},
    )
    fn.extra["rules"] = serialized
    quality_findings.extend(collection.findings)
    try:
        _finalize_semantic_frame(fn, ctx)
    except ValueError as error:
        quality_findings.append(AnalysisFinding(
            concern="semantic_frame",
            status="rejected",
            target_id=code_id,
            reasons=(str(error),),
            evidence_rule="semantic-frame/v1",
        ))
        fn.extra.pop("semantic_frame_artifact_hash", None)
        ctx.extra.pop("semantic_frame_artifact_hash", None)
    finding_records = [finding.to_dict() for finding in quality_findings]
    record_code_analysis_findings(fn, quality_findings, component=_STEP)
    finalize_function_meaning(fn, quality_findings)
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
    # ctx 에 나머지 분석 결과 부착 — 부모(PARENT_OF)의 table 입력.
    # (summary 는 자기 concern 완료 시 즉시 공개됨 — CALLS 의존 조기 해제.)
    ctx.extra["rules"] = fn.extra["rules"]
    return fn


def _finalize_semantic_frame(
    fn: CodeNode,
    ctx: AnalysisContext,
) -> None:
    plan = ctx.extra.get("_semantic_frame_plan")
    if plan is None:
        raise ValueError(f"semantic frame plan missing: {fn.id}")
    _profile, all_specs, _concerns = plan
    frame = fn.extra.get("_summary_semantic_frame")
    if frame is None:
        raise ValueError(f"summary semantic frame missing: {fn.id}")
    actual_slot_ids = set(frame.slots)
    required_slot_ids = {spec.slot_id for spec in all_specs}
    if actual_slot_ids != required_slot_ids:
        raise ValueError(
            f"semantic frame slot mismatch: missing={sorted(required_slot_ids - actual_slot_ids)} "
            f"extra={sorted(actual_slot_ids - required_slot_ids)}"
        )
    artifact_hash = persist_function_semantic_frame(frame)
    fn.extra["semantic_frame_artifact_hash"] = artifact_hash
    ctx.extra["semantic_frame_artifact_hash"] = artifact_hash


def _prepare_analysis_inputs(
    ctx: AnalysisContext,
    fn: CodeNode,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    cache: SourceFileCache,
    table_descriptions: dict[str, dict],
) -> tuple[dict, object]:
    """Prepare one canonical source/context packet and its branch inventory."""
    raw_body = exact_source_body(ctx, cache)
    inventory = build_control_inventory(fn, ctx, cache)
    # 상태 추적 없이 소스에 직접 쓰인 반환 표현식만 bottom-up 인터페이스로 전달한다.
    ctx.extra["return_expressions"] = sorted(inventory.return_expressions)
    ctx.extra["termination_effects"] = [
        {"line": line, "kind": kind}
        for line, kind, _source in inventory.termination_rows
    ]
    persist_branch_table(fn, inventory)
    persist_rule_audit(
        fn,
        inventory,
        condition_bundle=fn.extra.get("_resolved_rule_condition_bundle"),
    )
    persist_coverage(fn, inventory)
    profile, slot_specs = build_code_semantic_plan(
        fn,
        ctx,
        inventory=inventory,
        rule_rows=required_rule_rows(inventory),
        rule_owner_id=fn.id,
        child_nodes=(
            node for node in code_by_id.values() if node.parent_id == fn.id
        ),
    )
    concerns = semantic_concern_slots(slot_specs)
    ctx.extra["_semantic_frame_plan"] = (profile, slot_specs, concerns)
    values = {
        "raw_source_code": raw_body,
        "source_outline": render_source_outline(fn, code_by_id),
        "direct_table_usages": project_direct_table_usages(
            profile, table_descriptions,
        ),
        "referenced_symbols": _project_referenced_symbols(ctx),
        "external_source_text_by_id": {
            item.id: item.text for item in ctx.external_evidence
        },
        "callee_effects_by_id": direct_callee_effects_by_id(ctx, contexts),
        "control_branch_index": inventory.branch_index(),
    }
    return values, inventory
