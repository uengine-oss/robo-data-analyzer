"""관심사 fan-out 헬퍼 — ``analyze_code_step`` 이 코드노드당 1회 호출.

코드노드 1개당 서로 다른 LLM 관심사(병렬, 정상 경로 1~3회):
- ``code_summary`` → canonical summary + stereotype (항상)
- ``code_rule_meanings`` → Analyzer가 지정한 condition/effect atom 의미와 Rule 여부
  (Rule row와 atom이 있을 때만 — 없으면 skip)
- ``code_table_usage_meanings`` → access-specific function-table meanings
  (R/W 테이블이 있을 때만 — 없으면 skip)

Rule의 구조와 좌표는 parser-owned branch evidence에서 결정론으로 만들고, 모델은 배정된 atom의
업무 의미만 채운다. Example은 검증된 Rule을 결정론적으로 투영하므로 별도 LLM 호출이 없다.
각 관심사는 자기 근거만 한 번 받는다.

**KV 캐시 흐름**:
서버 KV cache는 동작하지만 API cached-token 필드는 0만 보고한다. 저장 prompt의 실제
바이트를 대조하면 세 LLM concern은 공통 system prefix만 공유하고 user prompt는 concern별로
갈라진다. 따라서 첫 summary 한 번으로 전역 system prefix를 워밍하되, 이후 코드노드에서
summary를 기다려도 node-specific prefix는 늘지 않는다. summary/rule/table-usage meaning은
독립 실행하고 summary가 끝나는 즉시 caller 의존만 조기 해제한다.

Framework와 DBMS 모두 현재 TARGET의 정확한 원문을 사용한다. 자식 원문을 생성 요약으로
치환하지 않으며, 필요한 direct callee 결과만 별도 구조화 문맥으로 한 번 전달한다.

각 호출은 Pydantic ``response_format`` 강제 — 자유 텍스트 응답 차단.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline.steps.step7_analyze_code.source_body import exact_source_body
from pipeline.steps.step7_analyze_code.rw_table_filter import (
    serialize_rules,
)
from pipeline.steps.step7_analyze_code.table_prompt_context import (
    dump_linked_table_descriptions,
)
from pipeline.models.data_object_usage import exact_direct_table_ids
from pipeline.steps.step7_analyze_code.summary_quality import (
    copied_callee_summary_id,
    missing_structural_summary_identities,
    normalize_canonical_summary,
    unsupported_code_comparison_claims,
)
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.analysis_finding import AnalysisFinding
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import (
    exclude_contradicted_stereotype,
    require_stereotype,
)
from llm.usage.cost_budget import CostBudget
from pipeline.models.code_analysis_output import (
    SummaryOutput,
)
from pipeline.models.summary_response_schema import (
    frame_summary_response_model,
)
from pipeline.models.semantic_frame import (
    mark_semantic_frame_insufficient,
    merge_semantic_frames,
    normalize_semantic_frame_response,
    profile_prompt_json,
    semantic_evidence_wire_bindings,
    slot_specs_json,
)
from pipeline.models.semantic_frame_projection import project_summary
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.models.function_table_usage_response_schema import (
    function_table_usage_prompt_assignments,
    function_table_usages_response_model,
    normalize_function_table_usages_response,
)
from pipeline.models.rule_meaning_frame import (
    build_rule_meaning_packet,
    project_rule_meanings,
    rule_meaning_response_model,
)
from pipeline.quality_gates.rule_obligations import required_rule_rows
from pipeline.steps.step7_analyze_code.rule_row_batching import (
    apply_rule_meaning_projections,
    mark_rule_meanings_unavailable,
    materialize_and_validate_rules,
)
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore
from pipeline.execution.analysis_handoff import (
    TABLE_USAGE_MEANINGS_LANE,
    RULES_LANE,
    SUMMARY_LANE,
    SUMMARY_WAKE_PRIORITY,
)
from pipeline.execution.semantic_artifacts import persist_function_semantic_frame
from pipeline.steps.step7_analyze_code.control_prompt import build_control_inventory
from pipeline.steps.step7_analyze_code.branch_audit import (
    persist_branch_table,
    persist_coverage,
)
from pipeline.quality_gates.rule_audit_artifact import persist_rule_audit
from pipeline.steps.step7_analyze_code.function_meaning import finalize_function_meaning
from pipeline.steps.step7_analyze_code.quality_findings import record_code_analysis_findings

_STEP = "step7.analyze_code.three_split"


def _assigned_external_source_texts(
    profile,
    slot_specs,
    external_by_chunk: dict,
) -> dict[str, str]:
    """Map request-local evidence keys to their exact external source text."""
    specs = tuple(slot_specs)
    assigned_refs = {
        ref
        for spec in specs
        for ref in (spec.target_ref, *spec.evidence_refs)
    }
    evidence_bindings = semantic_evidence_wire_bindings(specs)
    source_texts: dict[str, str] = {}
    for ref in sorted(assigned_refs):
        fact = profile.evidence[ref]
        if fact.kind != "EXTERNAL":
            continue
        chunk_id = str(fact.attributes.get("chunk_id") or "")
        source_text = external_by_chunk.get(chunk_id)
        if source_text is None:
            raise ValueError(
                f"assigned external evidence payload is missing: {chunk_id or fact.id}"
            )
        source_texts[evidence_bindings[ref]] = str(source_text)
    return source_texts


def _direct_callee_outcomes_by_id(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> dict[str, dict[str, str]]:
    """Return the direct callee outcomes that source and CALL facts cannot recover."""
    meanings: dict[str, dict[str, str]] = {}
    for callee_id in ctx.callees:
        callee = contexts.get(callee_id)
        if callee is None:
            continue
        meaning = (callee.summary or "").strip()
        if not meaning:
            continue
        meanings[callee_id] = {
            "callee": callee.signature or callee_id,
            "meaning": meaning,
        }
    return meanings


def _summary_prompt_values(profile, slot_specs, analysis_inputs: dict) -> dict:
    """Build one model packet from only its assigned evidence and exact source lines."""
    specs = tuple(slot_specs)
    assigned_refs = {
        ref
        for spec in specs
        for ref in (spec.target_ref, *spec.evidence_refs)
    }
    source_text = str(analysis_inputs.get("raw_source_code") or "")
    external_by_chunk = dict(analysis_inputs.get("external_source_text_by_id") or {})
    # Split only on LF so CR and a trailing empty physical line remain exact payload data.
    source_lines = source_text.split("\n") if source_text else []
    target_start = max(int(profile.target.start_line), 1)
    if not any(
        (spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY")
        or spec.family in {"STATEMENT", "CALL"}
        for spec in specs
    ):
        raise ValueError("summary assignment lacks a whole-target owner slot")
    source_packet = "\n".join(
        f"{target_start + index} | {source_lines[index]}"
        for index in range(len(source_lines))
    )

    external_source_texts = _assigned_external_source_texts(
        profile,
        specs,
        external_by_chunk,
    )
    callee_meanings_by_id = dict(analysis_inputs.get("callee_outcomes_by_id") or {})
    callee_contexts = list(callee_meanings_by_id.values())
    def _dump(value: object) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
    return {
        "code_body": source_packet,
        "structural_profile": profile_prompt_json(profile, specs),
        "applicable_slots": slot_specs_json(specs),
        "callee_contexts": _dump(callee_contexts),
        "external_evidence": _dump(external_source_texts),
    }


def _table_usage_meaning_values(
    profile,
    slot_specs,
    direct_reads,
    direct_writes,
    table_descriptions: dict,
    analysis_inputs: dict,
) -> dict:
    """Build one non-redundant Table meaning packet."""
    specs = tuple(slot_specs)
    return {
        "assigned_table_usages": json.dumps(
            function_table_usage_prompt_assignments(
                profile, specs, direct_reads, direct_writes,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "table_contexts": dump_linked_table_descriptions(
            tuple(dict.fromkeys((*direct_reads, *direct_writes))),
            table_descriptions,
        ),
        "code_body": analysis_inputs["numbered_source_code"],
    }


async def _run_frame_summary(
    *, profile, slot_specs, strategy_name: str, analysis_inputs: dict, invoke,
) -> SummaryOutput:
    specs = tuple(slot_specs)
    if not specs:
        raise ValueError("summary concern requires at least one semantic slot")
    values = _summary_prompt_values(profile, specs, analysis_inputs)
    output = await invoke(
        values,
        frame_summary_response_model(strategy_name, specs),
    )
    frame = normalize_semantic_frame_response(output, profile, specs)
    return project_summary(
        frame,
        summary=str(output.summary),
        stereotype=str(output.stereotype),
    )


async def analyze_code_three_split(
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

    흐름: summary/rule-atom/table-usage meaning LLM을 독립 실행하고 Example은 검증 Rule에서 투영한다.
    ``precomputed_summary``가 있으면 summary만 재호출하지 않는다. 확정된 callee summary는
    summary와 Rule 입력에는 각 concern이 직접 소비하는 callee 결과만 한 번 남는다.

    Args:
        table_descriptions: ``{table_fqn: DDL 설명}`` — reads/writes 렌더에 의미 부여.
        precomputed_summary: 테스트나 명시적 호출자가 이미 얻은 Summary 결과.
        precomputed_summary_supplied: 워밍 호출이 이미 끝났는지 표시. 워밍 실패(None)도
            여기서 같은 summary를 다시 호출하지 않아 exact-once 관심사 계약을 지킨다.
        summary_warm_event: 첫 Summary 완료 뒤 다른 Summary를 시작시키는 run-scoped 신호.
            Rule과 Table concern은 이 신호를 기다리지 않는다.
        is_summary_warmup: 이 노드의 Summary가 신호를 여는 첫 호출인지 표시.
        on_summary_ready: summary 확정(경계 검사 후) 직후 호출되는 스케줄러 신호 —
            caller를 table concern 완료를 기다리지 않고 조기 투입하게 한다.

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

    # 대상 사실의 한 정의처. 각 LLM concern template은 자기 책임 필드만 선택하고,
    # inventory는 Rule 물질화·감사의 구조 정답지다.
    analysis_inputs, inventory = _prepare_analysis_inputs(
        ctx,
        fn,
        contexts,
        code_by_id,
        source_cache or SourceFileCache(),
    )
    prompts_dir = Path(strategy.prompts_dir)

    # split 단위 wrapper — code_id 캡처 closure (응집 ★).
    async def _track_split(
        sub_action: str,
        invoke,
        *,
        priority: int = 0,
        lane: str,
        payload_chars: int = 0,
    ):
        async with track_processing("CodeNode", code_id, sub_action=sub_action):
            queued_at = time.monotonic()
            waiting_before = llm_slots.waiting
            async with llm_slots.at(priority, lane):
                log_info(
                    _STEP,
                    "llm_slot_acquired",
                    code_id=code_id,
                    concern=sub_action,
                    lane=lane,
                    priority=priority,
                    payload_chars=payload_chars,
                    queue_wait_ms=round((time.monotonic() - queued_at) * 1000, 3),
                    waiting_before=waiting_before,
                    slots_in_use=llm_slots.in_use,
                    lane_slots_in_use=llm_slots.in_use_for(lane),
                    slots_capacity=llm_slots.capacity,
                )
                return await invoke()

    # Server logs confirm KV caching, but recorded prompt bytes show that the three
    # concerns share only the global system prompt; their user prompts diverge immediately.
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
                async def _invoke(values, response_model):
                    return await _track_split(
                        "split_summary",
                        lambda: run_yaml_rule(
                            prompts_dir=prompts_dir,
                            rule_name="code_summary",
                            user_values=values,
                            response_model=response_model,
                            budget=cost_budget,
                        ),
                        priority=SUMMARY_WAKE_PRIORITY,
                        lane=SUMMARY_LANE,
                        payload_chars=sum(len(str(value)) for value in values.values()),
                    )

                summary_out = await _run_frame_summary(
                    profile=profile,
                    slot_specs=slot_specs,
                    strategy_name=strategy.name,
                    analysis_inputs=analysis_inputs,
                    invoke=_invoke,
                )
            except ModelOutputError as error:
                summary_out = error

        if is_summary_warmup and summary_warm_event is not None:
            summary_warm_event.set()

        findings: list[AnalysisFinding] = []
        if isinstance(summary_out, ModelOutputError):
            findings.append(AnalysisFinding(
                concern="summary",
                status="unavailable",
                target_id=code_id,
                reasons=(str(summary_out),),
                evidence_rule=summary_out.rule_name,
            ))
        else:
            _publish_semantic_frame(fn, ctx, summary_out)
            if summary_out.evidence_status == "insufficient":
                fn.summary = None
                fn.stereotype = None
                ctx.summary = ""
                ctx.extra["summary_status"] = "needs_context"
                findings.append(AnalysisFinding(
                    concern="summary",
                    status="unverified",
                    target_id=code_id,
                    reasons=tuple(summary_out.missing_context),
                    candidate=summary_out.model_dump(),
                    evidence_rule="code_summary",
                ))
                if on_summary_ready is not None:
                    on_summary_ready(code_id)
                return findings
            summary_out.summary = normalize_canonical_summary(summary_out.summary)
            callee_outcomes = _direct_callee_outcomes_by_id(ctx, contexts)
            copied_from = copied_callee_summary_id(
                summary_out.summary,
                (
                    (callee_id, item["meaning"])
                    for callee_id, item in callee_outcomes.items()
                ),
            )
            if copied_from is not None:
                # Keep the rejected candidate in the finding, but never publish it
                # to display/search or to a caller's callee-summary input.
                fn.summary = None
                fn.stereotype = None
                ctx.summary = ""
                ctx.extra["summary_status"] = "rejected"
                _degrade_summary_semantic_frame(
                    fn, ctx, "validated summary unavailable: callee summary copy rejected",
                )
                findings.append(AnalysisFinding(
                    concern="summary",
                    status="rejected",
                    target_id=code_id,
                    reasons=(
                        "summary가 직접 callee canonical summary와 동일하여 "
                        "현재 대상의 책임을 소유하지 않음",
                    ),
                    candidate={
                        "summary": summary_out.summary,
                        "copied_callee_id": copied_from,
                    },
                    evidence_rule="code_summary",
                ))
            else:
                missing_identities = missing_structural_summary_identities(
                    summary_out.summary, fn,
                )
                if missing_identities:
                    fn.summary = None
                    fn.stereotype = None
                    ctx.summary = ""
                    ctx.extra["summary_status"] = "rejected"
                    _degrade_summary_semantic_frame(
                        fn, ctx,
                        "validated summary unavailable: direct AST identities omitted",
                    )
                    findings.append(AnalysisFinding(
                        concern="summary",
                        status="rejected",
                        target_id=code_id,
                        reasons=(
                            "summary omitted direct AST physical identities: "
                            + ", ".join(missing_identities),
                        ),
                        candidate={
                            "summary": summary_out.summary,
                            "missing_structural_identities": list(missing_identities),
                        },
                        evidence_rule="code_summary",
                    ))
                else:
                    unsupported_comparisons = unsupported_code_comparison_claims(
                        summary_out.summary,
                        "\n".join((
                            analysis_inputs["raw_source_code"],
                            analysis_inputs["control_branch_index"],
                            fn.expression or "",
                        )),
                        ctx.referenced_symbols,
                    )
                    if unsupported_comparisons:
                        fn.summary = None
                        fn.stereotype = None
                        ctx.summary = ""
                        ctx.extra["summary_status"] = "rejected"
                        _degrade_summary_semantic_frame(
                            fn, ctx,
                            "validated summary unavailable: comparison operands changed",
                        )
                        findings.append(AnalysisFinding(
                            concern="summary",
                            status="rejected",
                            target_id=code_id,
                            reasons=(
                                "summary changed explicit comparison operands: "
                                + "; ".join(unsupported_comparisons),
                            ),
                            candidate={
                                "summary": summary_out.summary,
                                "unsupported_code_comparisons": list(
                                    unsupported_comparisons
                                ),
                            },
                            evidence_rule="code_summary",
                        ))
                    else:
                        contradiction = _apply_summary_fields(fn, ctx, summary_out, strategy)
                        if contradiction is not None:
                            findings.append(contradiction)
                        ctx.summary = summary_out.summary
                        ctx.extra["summary_status"] = (
                            "partial" if summary_out.evidence_status == "partial" else "accepted"
                        )
                        if summary_out.evidence_status == "partial":
                            findings.append(AnalysisFinding(
                                concern="summary",
                                status="unverified",
                                target_id=code_id,
                                reasons=tuple(summary_out.missing_context),
                                candidate=summary_out.model_dump(),
                                evidence_rule="code_summary",
                            ))
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
        conditions = collection.evidence.get("conditions") or {}
        effects = collection.evidence.get("effects") or {}
        rows = collection.evidence.get("rows") or {}
        if not collection.rules:
            return collection
        if not conditions and not effects:
            return mark_rule_meanings_unavailable(
                collection,
                target_scope=code_id,
                reason="Rule row has no condition/effect atom from which to derive business meaning",
                count_call=False,
            )
        packet = build_rule_meaning_packet(
            collection.evidence,
            numbered_source=analysis_inputs["numbered_source_code"],
            referenced_symbols=ctx.referenced_symbols,
            callee_outcomes_by_id=_direct_callee_outcomes_by_id(ctx, contexts),
            table_descriptions=table_descriptions or {},
            api_contracts=tuple(ctx.extra.get("api_contracts") or ()),
        )
        values = {
            # Audit identity is consumed by the prompt recorder only.  Because the
            # Rule template has no {target_id} placeholder, it never reaches the model.
            "target_id": code_id,
            "rule_packet": json.dumps(
                packet,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        try:
            output = await _track_split(
                "split_rules",
                lambda: run_yaml_rule(
                    prompts_dir=prompts_dir,
                    rule_name="code_rule_meanings",
                    user_values=values,
                    response_model=rule_meaning_response_model(
                        conditions, effects, rows,
                    ),
                    budget=cost_budget,
                ),
                lane=RULES_LANE,
                payload_chars=len(values["rule_packet"]),
            )
        except ModelOutputError as error:
            return mark_rule_meanings_unavailable(
                collection,
                target_scope=code_id,
                reason=str(error),
            )
        return apply_rule_meaning_projections(
            collection,
            project_rule_meanings(output, collection.evidence),
            target_scope=code_id,
        )

    gather_map: dict[str, Any] = {
        "summary": _collect_summary(),
        "rules": _collect_rule_meanings(),
    }
    usage_reads = exact_direct_table_ids(ctx, "READ")
    usage_writes = exact_direct_table_ids(ctx, "WRITE")
    if usage_reads or usage_writes:
        profile, _all_specs, concerns = ctx.extra["_semantic_frame_plan"]
        usage_specs = concerns["table_usage_meanings"]
        usage_model = function_table_usages_response_model(
            profile, usage_specs, usage_reads, usage_writes,
        )
        async def _collect_table_usage_meanings():
            try:
                usage_values = _table_usage_meaning_values(
                    profile,
                    usage_specs,
                    usage_reads,
                    usage_writes,
                    table_descriptions or {},
                    analysis_inputs,
                )
                output = await _track_split(
                    "split_table_usage_meanings",
                    lambda: run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="code_table_usage_meanings",
                        user_values=usage_values,
                        response_model=usage_model,
                        budget=cost_budget,
                    ),
                    payload_chars=sum(
                        len(str(value)) for value in usage_values.values()
                    ),
                    lane=TABLE_USAGE_MEANINGS_LANE,
                )
                return output, None
            except ModelOutputError as error:
                return None, error

        gather_map["table_usage_meanings"] = _collect_table_usage_meanings()

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
    usage_result = done.get("table_usage_meanings")
    usage_output = usage_result[0] if usage_result is not None else None
    if usage_result is not None and usage_result[1] is not None:
        error = usage_result[1]
        quality_findings.append(AnalysisFinding(
            concern="function_table_usage_meanings",
            status="unavailable",
            target_id=code_id,
            reasons=(str(error),),
            evidence_rule=error.rule_name,
        ))
    if usage_output is not None:
        usage_output = normalize_function_table_usages_response(
            usage_output,
            profile,
            usage_specs,
            usage_reads,
            usage_writes,
        )

    obligation_rows = len(required_rule_rows(inventory))

    # fan-in 머지 → CodeNode 에 적용 (#1 의미 + 결정론 검색 필드는 이미 확정됐다).
    # 자연어 규칙 시절의 품질·근거성 사후 관측은 은퇴 — 결정론 채점(grade_rules)이
    # 생성 시점에 행 단위로 대체했다(spec 116).
    function_table_usages = (
        [item.model_dump() for item in usage_output.function_table_usages]
        if usage_output else []
    )
    if function_table_usages:
        fn.extra["function_table_usages"] = function_table_usages
    else:
        fn.extra.pop("function_table_usages", None)
    direct_column_evidence = dict(
        ctx.extra.get("column_usage_candidates") or {}
    )
    if direct_column_evidence:
        fn.extra["direct_column_usage_evidence_by_table"] = direct_column_evidence
    else:
        fn.extra.pop("direct_column_usage_evidence_by_table", None)
    serialized, unresolved_tables = serialize_rules(
        collection.rules, ctx.reads, ctx.writes,
        ctx.extra.get("table_op"), ctx.extra.get("table_identity_aliases") or {},
    )
    fn.extra["rules"] = serialized
    quality_findings.extend(collection.findings)
    try:
        _finalize_semantic_frame(fn, ctx, usage_output)
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


def _apply_summary_fields(
    fn: CodeNode,
    ctx: AnalysisContext,
    summary_out: SummaryOutput,
    strategy: Any,
) -> AnalysisFinding | None:
    """#1 summary 에서만 나오는 코드노드 필드를 확정한다 (spec 096).

    #2(table usage)·#3(rules)는 이 의미/검색 필드를 읽지도 쓰지도 않는다. 그래서 노드 완료가
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


def _publish_semantic_frame(
    fn: CodeNode,
    ctx: AnalysisContext,
    summary_out: SummaryOutput,
) -> None:
    frame = summary_out.semantic_frame
    if frame is None:
        raise ValueError(f"frame-first summary is missing semantic frame: {fn.id}")
    fn.extra["_summary_semantic_frame"] = frame
    ctx.extra["_summary_semantic_frame"] = frame


def _degrade_summary_semantic_frame(
    fn: CodeNode, ctx: AnalysisContext, reason: str,
) -> None:
    frame = fn.extra.get("_summary_semantic_frame")
    if frame is None:
        raise ValueError(f"summary semantic frame missing while rejecting output: {fn.id}")
    degraded = mark_semantic_frame_insufficient(frame, (reason,))
    fn.extra["_summary_semantic_frame"] = degraded
    ctx.extra["_summary_semantic_frame"] = degraded


def _finalize_semantic_frame(
    fn: CodeNode,
    ctx: AnalysisContext,
    usage_output: object | None,
) -> None:
    plan = ctx.extra.get("_semantic_frame_plan")
    if plan is None:
        raise ValueError(f"semantic frame plan missing: {fn.id}")
    _profile, all_specs, _concerns = plan
    frames = [fn.extra.get("_summary_semantic_frame")]
    if usage_output is not None:
        frames.append(getattr(usage_output, "semantic_frame", None))
    merged = merge_semantic_frames(
        *frames,
        required_slot_ids=(spec.slot_id for spec in all_specs),
    )
    artifact_hash = persist_function_semantic_frame(merged)
    fn.extra["semantic_frame_artifact_hash"] = artifact_hash
    ctx.extra["semantic_frame_artifact_hash"] = artifact_hash


def _prepare_analysis_inputs(
    ctx: AnalysisContext,
    fn: CodeNode,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    cache: SourceFileCache,
) -> tuple[dict, object]:
    """Prepare one canonical source/context packet and its branch inventory."""
    excerpt = cache.excerpt(ctx.file_path, ctx.start_line, ctx.end_line)
    raw_body = exact_source_body(ctx, cache)
    inventory = build_control_inventory(fn, ctx, cache)
    # 상태 추적 없이 소스에 직접 쓰인 반환 표현식만 bottom-up 인터페이스로 전달한다.
    ctx.extra["return_expressions"] = sorted(inventory.return_expressions)
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
        "numbered_source_code": excerpt.numbered_text,
        "external_source_text_by_id": {
            item.id: item.text for item in ctx.external_evidence
        },
        "callee_outcomes_by_id": _direct_callee_outcomes_by_id(ctx, contexts),
        "control_branch_index": inventory.branch_index(),
    }
    return values, inventory
