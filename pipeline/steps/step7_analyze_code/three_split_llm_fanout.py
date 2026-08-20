"""관심사 fan-out 헬퍼 — ``analyze_code_step`` 이 코드노드당 1회 호출.

코드노드 1개당 LLM 호출 (병렬, 1~2 번):
- ``code_summary``        → canonical summary + stereotype + questions (항상)
- ``code_table_insights`` → table_insights (R/W 테이블 있을 때만 — 없으면 skip)

Rule/Example은 parser-owned branch evidence에서 결정론으로 물질화한다(LLM 0).
모델 concern은 summary와 direct table insight뿐이며, 각 concern은 필요한 증거만 받는다.

**KV 캐시 흐름**:
서버 KV cache는 동작하지만 API cached-token 필드는 0만 보고한다. 저장 prompt의 실제
바이트를 대조하면 두 LLM concern은 공통 system prefix만 공유하고 user prompt는 concern별로
갈라진다. 따라서 첫 summary 한 번으로 전역 system prefix를 워밍하되, 이후 코드노드에서
summary를 기다려도 node-specific prefix는 늘지 않는다. summary/table insight는 독립 실행하고
Rule은 같은 inventory에서 즉시 결정론 물질화한다. summary가 끝나는 즉시 caller 의존만 조기 해제한다.

DBMS 부모 range 는 본문이 ``compact_body`` (자식 구간 → 자식 summary 치환) — 자식이
먼저 분석되어 그 결과가 부모 입력으로 합류. Framework 는 평면이라 raw 본문.

각 호출은 Pydantic ``response_format`` 강제 — 자유 텍스트 응답 차단.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
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
from pipeline.models.data_object_usage import dump_data_object_usages, exact_direct_table_ids
from pipeline.steps.step7_analyze_code.summary_quality import (
    copied_callee_summary_id,
    missing_structural_summary_identities,
    normalize_canonical_summary,
    unsupported_code_comparison_claims,
)
from pipeline.models.analysis_context import AnalysisContext, dump_external_evidence, dump_referenced_symbols
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
    slot_specs_json,
)
from pipeline.models.semantic_frame_projection import project_summary
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.models.table_insight_response_schema import (
    frame_table_insights_response_model,
    normalize_frame_table_insights_response,
)
from pipeline.quality_gates.rule_obligations import required_rule_rows
from pipeline.steps.step7_analyze_code.rule_row_batching import materialize_and_validate_rules
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore
from shared.config.settings import SETTINGS
from pipeline.execution.analysis_handoff import (
    INSIGHTS_LANE,
    SUMMARY_LANE,
    SUMMARY_WAKE_PRIORITY,
)
from pipeline.steps.step7_analyze_code.control_prompt import build_control_inventory
from pipeline.steps.step7_analyze_code.model_source_projection import (
    project_model_source_literals,
)
from pipeline.steps.step7_analyze_code.branch_audit import (
    persist_branch_table,
    persist_coverage,
)
from pipeline.quality_gates.rule_audit_artifact import persist_rule_audit
from pipeline.steps.step7_analyze_code.quality_findings import apply_analysis_findings

_STEP = "step7.analyze_code.three_split"


def _summary_slot_batches(
    slot_specs,
    *,
    profile=None,
    common: dict | None = None,
    max_input_tokens: int | None = None,
):
    """Return the complete target-owned summary assignment without a semantic cap.

    The five FUNCTION roles are a closed taxonomy, not a limit on evidence-anchored
    obligations.  Transport-sized partitioning is applied with the exact profile/source
    closure at invocation time; this helper only proves exact-once assignment.
    """
    specs = tuple(slot_specs)
    if not specs:
        raise ValueError("summary concern requires at least one semantic slot")
    primary = tuple(spec for spec in specs if spec.family in {"FUNCTION", "STATEMENT"})
    remaining = tuple(spec for spec in specs if spec not in primary)
    ordered = (*primary, *remaining)
    if (profile is None) != (common is None):
        raise ValueError("summary batching requires profile and common together")
    if profile is None:
        batches = (tuple(ordered),)
    else:
        limit = max_input_tokens or SETTINGS.analysis_pipeline.batch_max_tokens
        if limit <= 0:
            raise ValueError("summary batch input token budget must be positive")
        groups: list[tuple] = []
        pending: list = []
        for spec in ordered:
            candidate = (*pending, spec)
            values = _summary_batch_values(profile, candidate, common or {})
            variable_tokens = _estimate_prompt_tokens("\n".join(
                str(values[name]) for name in (
                    "structural_profile", "applicable_slots", "code_body",
                    "referenced_symbols", "external_evidence",
                    "data_object_usages", "branch_index",
                )
            ))
            if pending and variable_tokens > limit:
                groups.append(tuple(pending))
                pending = [spec]
            else:
                pending.append(spec)
        if pending:
            groups.append(tuple(pending))
        batches = tuple(groups)
    assigned = [spec.slot_id for spec in ordered]
    if len(assigned) != len(set(assigned)) or set(assigned) != {
        spec.slot_id for spec in specs
    }:
        raise ValueError("summary slot batching lost or duplicated a slot")
    return batches


def _estimate_prompt_tokens(text: str) -> int:
    """Conservative deterministic estimate used only to split exact input closures."""
    ascii_count = sum(1 for char in text if ord(char) < 128)
    other_count = len(text) - ascii_count
    return (ascii_count + 3) // 4 + (other_count * 2 + 2) // 3


def _summary_batch_values(profile, batch_specs, common: dict) -> dict:
    """Build one model packet from only its assigned evidence and exact source lines."""
    specs = tuple(batch_specs)
    assigned_refs = {
        ref
        for spec in specs
        for ref in (spec.target_ref, *spec.evidence_refs)
    }
    facts = [profile.evidence[ref] for ref in sorted(assigned_refs)]
    source_text = str(common.get("raw_code_body") or "")
    external_by_chunk = dict(common.get("_external_evidence_by_chunk") or {})
    public_common = {
        key: value for key, value in common.items() if not str(key).startswith("_")
    }
    # Split only on LF so CR and a trailing empty physical line remain exact payload data.
    source_lines = source_text.split("\n") if source_text else []
    target_start = max(int(profile.target.start_line), 1)
    owns_whole_target = any(
        (spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY")
        or spec.family in {"STATEMENT", "CALL"}
        for spec in specs
    )
    if owns_whole_target:
        selected_indexes = set(range(len(source_lines)))
    else:
        selected_indexes: set[int] = set()
        for fact in facts:
            if fact.kind in {"TARGET", "EXTERNAL"} or fact.start_line <= 0:
                continue
            start = max(fact.start_line - target_start, 0)
            stop = min(fact.end_line - target_start + 1, len(source_lines))
            selected_indexes.update(range(start, stop))
    source_records = [
        {"line": target_start + index, "text": source_lines[index]}
        for index in sorted(selected_indexes)
    ]
    source_packet = json.dumps(
        {
            "schema": "source-closure/v1",
            "target_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "lines": source_records,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    by_kind: dict[str, list[dict]] = {}
    for fact in facts:
        by_kind.setdefault(fact.kind, []).append(
            fact.model_dump(mode="json")
        )
    data_facts = by_kind.get("DATA_OBJECT", [])
    external_payloads: list[dict] = []
    for fact in by_kind.get("EXTERNAL", []):
        chunk_id = str(fact["attributes"].get("chunk_id") or "")
        payload = external_by_chunk.get(chunk_id)
        if payload is None:
            raise ValueError(
                f"assigned external evidence payload is missing: {chunk_id or fact['id']}"
            )
        external_payloads.append(dict(payload))
    reads = [
        fact for fact in data_facts
        if str(fact["attributes"].get("access") or "").upper() == "READ"
    ]
    writes = [
        fact for fact in data_facts
        if str(fact["attributes"].get("access") or "").upper() == "WRITE"
    ]
    table_ops = {
        str(fact["attributes"].get("table_id") or ""): fact["attributes"].get("operation")
        for fact in data_facts
        if str(fact["attributes"].get("table_id") or "")
    }

    def _dump(value: object) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
    selected_text = "\n".join(record["text"] for record in source_records)
    return {
        **public_common,
        "id": profile.target.id,
        "signature": str(
            profile.evidence[next(
                ref for ref in assigned_refs
                if profile.evidence[ref].kind == "TARGET"
            )].attributes.get("signature") or ""
        ),
        "source_file": profile.target.file,
        "source_range": f"{profile.target.start_line}-{profile.target.end_line}",
        "code_body": source_packet,
        "raw_code_body": selected_text,
        "numbered_raw_code_body": source_packet,
        "structural_profile": profile_prompt_json(profile, specs),
        "applicable_slots": slot_specs_json(specs),
        "reads": _dump(reads),
        "writes": _dump(writes),
        "direct_reads": _dump(reads),
        "direct_writes": _dump(writes),
        "table_ops": _dump(table_ops),
        "data_object_usages": _dump(data_facts),
        "column_usage_candidates": "{}",
        "referenced_symbols": _dump(by_kind.get("SYMBOL", [])),
        "external_evidence": _dump(external_payloads),
        "callee_summaries": _dump(by_kind.get("CALL", [])),
        "branch_index": _dump([
            *by_kind.get("CONTROL", []), *by_kind.get("RULE", []),
        ]),
        "api_contracts": "[]",
        "codebase_notes": "(없음: 파생 prose는 의미 근거로 전달하지 않음)",
        "module_intent": "(없음: 파생 prose는 의미 근거로 전달하지 않음)",
        "ancestor_context": "(없음: 현재 evidence closure 밖)",
    }


async def _run_bounded_frame_summary(
    *, profile, slot_specs, strategy_name: str, common: dict, invoke,
    max_input_tokens: int | None = None,
) -> SummaryOutput:
    stereotype_owners = tuple(
        spec for spec in slot_specs
        if spec.family == "FUNCTION" and spec.role == "RESPONSIBILITY"
    )
    if len(stereotype_owners) > 1:
        raise ValueError(
            "summary stereotype has multiple FUNCTION.RESPONSIBILITY owners"
        )
    stereotype_owner_id = (
        stereotype_owners[0].slot_id
        if stereotype_owners else tuple(slot_specs)[0].slot_id
    )

    async def _collect(batch_specs, batch_label):
        owns_stereotype = any(
            spec.slot_id == stereotype_owner_id for spec in batch_specs
        )
        values = _summary_batch_values(profile, batch_specs, common)
        try:
            output = await invoke(
                values,
                frame_summary_response_model(
                    strategy_name,
                    batch_specs,
                    include_stereotype=owns_stereotype,
                ),
                batch_label,
            )
        except ModelOutputError as error:
            # A deadline after partial generation is not safe to retry unchanged.  A
            # repetition abort can also be isolated to one slot in a multi-slot forced
            # response.  Splitting changes both the required schema and prompt evidence
            # closure, so each smaller request is independently validated.  A failing
            # singleton remains an explicit model-output failure.
            if error.kind not in {"deadline", "length", "repetition"} or len(batch_specs) == 1:
                raise
            midpoint = len(batch_specs) // 2
            return (
                *await _collect(batch_specs[:midpoint], f"{batch_label}a"),
                *await _collect(batch_specs[midpoint:], f"{batch_label}b"),
            )
        frame = normalize_semantic_frame_response(output, profile, batch_specs)
        return ((frame, str(getattr(output, "stereotype", ""))),)

    collected = []
    for batch_index, batch_specs in enumerate(_summary_slot_batches(
        slot_specs,
        profile=profile,
        common=common,
        max_input_tokens=max_input_tokens,
    ), start=1):
        collected.extend(await _collect(batch_specs, str(batch_index)))
    frames = [frame for frame, _stereotype in collected]
    stereotypes = [value for _frame, value in collected if value]
    if len(stereotypes) != 1:
        raise ValueError(
            "summary stereotype must be produced exactly once across slot batches"
        )
    stereotype = stereotypes[0]
    merged = merge_semantic_frames(
        *frames,
        required_slot_ids=(spec.slot_id for spec in slot_specs),
    )
    return project_summary(merged, stereotype=stereotype)


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
        ctx, fn, contexts, code_by_id, codebase_notes,
        module_intents or {}, table_descriptions or {},
        source_cache or SourceFileCache(),
    )
    async def _call() -> SummaryOutput:
        async with track_processing("CodeNode", code_id, sub_action="warmup_summary"):
            profile, _all_specs, concerns = ctx.extra["_semantic_frame_plan"]
            slot_specs = concerns["summary"]

            async def _invoke(values, response_model, _batch_label):
                async with llm_slots.at(SUMMARY_WAKE_PRIORITY, SUMMARY_LANE):
                    return await run_yaml_rule(
                        prompts_dir=Path(strategy.prompts_dir),
                        rule_name="code_summary",
                        user_values=values,
                        response_model=response_model,
                        budget=cost_budget,
                    )

            return await _run_bounded_frame_summary(
                profile=profile,
                slot_specs=slot_specs,
                strategy_name=strategy.name,
                common=common,
                invoke=_invoke,
            )

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
    """코드노드 1개의 관심사 분석 결과를 ``CodeNode``에 적용해 반환.

    흐름: summary/table insight LLM과 결정론 Rule 물질화를 독립 실행한다.
    ``precomputed_summary``가 있으면 summary만 재호출하지 않는다. 확정된 callee summary는
    summary/table 입력에 남고 Rule은 parser-owned inventory만 사용한다.

    Args:
        codebase_notes: ``ScanResult.codebase_notes`` — prefix 공유로 캐시 적중.
        module_intents: ``{module_id: intent}`` — module_pre_analysis(M9) 결과.
        table_descriptions: ``{table_fqn: DDL 설명}`` — reads/writes 렌더에 의미 부여.
        precomputed_summary: Round 첫 코드노드의 워밍 결과.
        precomputed_summary_supplied: 워밍 호출이 이미 끝났는지 표시. 워밍 실패(None)도
            여기서 다시 호출하지 않아 concern 최대 2회 계약을 지킨다.
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
    common, inventory = _build_user_values(
        ctx, fn, contexts, code_by_id, codebase_notes,
        module_intents or {}, table_descriptions or {},
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

    # Server logs confirm KV caching, but recorded prompt bytes show that summary/table
    # share only the global system prompt; their user prompts diverge after 3 chars.
    # The one global warmup remains, while per-node concerns can start independently.
    async def _collect_summary():
        if precomputed_summary_supplied:
            summary_out = precomputed_summary
            if summary_out is None:
                raise RuntimeError(f"precomputed summary missing: {code_id}")
        else:
            try:
                profile, _all_specs, concerns = ctx.extra["_semantic_frame_plan"]
                slot_specs = concerns["summary"]
                async def _invoke(values, response_model, batch_label):
                    return await _track_split(
                        f"split_summary_{batch_label}",
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

                summary_out = await _run_bounded_frame_summary(
                    profile=profile,
                    slot_specs=slot_specs,
                    strategy_name=strategy.name,
                    common=common,
                    invoke=_invoke,
                )
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
            _publish_semantic_frame(fn, ctx, summary_out)
            fn.extra["summary_evidence_status"] = summary_out.evidence_status
            fn.extra["summary_missing_context"] = list(summary_out.missing_context)
            ctx.extra["summary_evidence_status"] = summary_out.evidence_status
            ctx.extra["summary_missing_context"] = list(summary_out.missing_context)
            if summary_out.evidence_status == "insufficient":
                fn.summary = None
                fn.stereotype = None
                fn.extra["questions"] = [q.model_dump() for q in summary_out.questions]
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
            callee_items = _callee_interface_items(ctx, contexts)
            copied_from = copied_callee_summary_id(
                summary_out.summary,
                (
                    (item["callee_id"], item["summary"])
                    for item in callee_items
                ),
            )
            if copied_from is not None:
                # Keep the rejected candidate in the finding, but never publish it
                # to display/search or to a caller's callee-summary input.
                fn.summary = None
                fn.stereotype = None
                fn.extra["questions"] = []
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
                    fn.extra["questions"] = []
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
                            common["raw_code_body"],
                            common["branch_index"],
                            fn.expression or "",
                        )),
                        ctx.referenced_symbols,
                    )
                    if unsupported_comparisons:
                        fn.summary = None
                        fn.stereotype = None
                        fn.extra["questions"] = []
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
        source=common["raw_code_body"],
        allowed_tables=sorted(set(ctx.reads) | set(ctx.writes)),
        read_tables=sorted(set(ctx.reads)),
        write_tables=sorted(set(ctx.writes)),
        table_ops=ctx.extra.get("table_op") or {},
        table_aliases=ctx.extra.get("table_identity_aliases") or {},
    )

    gather_map: dict[str, Any] = {
        "summary": _collect_summary(),
    }
    insight_reads = exact_direct_table_ids(ctx, "READ")
    insight_writes = exact_direct_table_ids(ctx, "WRITE")
    if insight_reads or insight_writes:
        profile, _all_specs, concerns = ctx.extra["_semantic_frame_plan"]
        insight_specs = concerns["insights"]
        insight_model = frame_table_insights_response_model(
            profile,
            insight_specs,
            insight_reads,
            insight_writes,
            ctx.extra.get("table_op") or {},
        )
        async def _collect_insights():
            try:
                insight_values = {
                    **common,
                    "structural_profile": profile_prompt_json(profile, insight_specs),
                    "applicable_slots": slot_specs_json(insight_specs),
                }
                output = await _track_split(
                    "split_insights",
                    lambda: run_yaml_rule(
                        prompts_dir=prompts_dir, rule_name="code_table_insights",
                        user_values=insight_values,
                        response_model=insight_model,
                        budget=cost_budget,
                    ),
                    payload_chars=sum(
                        len(str(value)) for value in insight_values.values()
                    ),
                    lane=INSIGHTS_LANE,
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
        insights_out = normalize_frame_table_insights_response(
            insights_out,
            profile,
            insight_specs,
            insight_reads,
            insight_writes,
            ctx.extra.get("column_usage_candidates") or {},
        )

    obligation_rows = len(required_rule_rows(inventory))

    # fan-in 머지 → CodeNode 에 적용 (#1 의미 + 결정론 검색 필드는 이미 확정됐다).
    # 자연어 규칙 시절의 품질·근거성 사후 관측은 은퇴 — 결정론 채점(grade_rules)이
    # 생성 시점에 행 단위로 대체했다(spec 116).
    fn.extra["table_insights"] = (
        [t.model_dump() for t in insights_out.table_insights] if insights_out else []
    )
    fn.extra["_insight_semantic_frame"] = (
        insights_out.semantic_frame if insights_out else None
    )
    serialized, unresolved_tables = serialize_rules(
        collection.rules, fn.signature or fn.id, ctx.reads, ctx.writes,
        ctx.extra.get("table_op"), ctx.extra.get("table_identity_aliases") or {},
    )
    fn.extra["rules"] = serialized
    quality_findings.extend(collection.findings)
    try:
        _finalize_semantic_frame(fn, ctx, insights_out)
    except ValueError as error:
        quality_findings.append(AnalysisFinding(
            concern="semantic_frame",
            status="rejected",
            target_id=code_id,
            reasons=(str(error),),
            evidence_rule="semantic-frame/v1",
        ))
        fn.extra.pop("semantic_frame", None)
        fn.extra.pop("semantic_frame_schema", None)
        ctx.extra.pop("semantic_frame", None)
        ctx.extra.pop("semantic_frame_schema", None)
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
    # ctx 에 나머지 분석 결과 부착 — 부모(PARENT_OF)의 table 입력.
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
    fn.extra["summary_evidence_status"] = summary_out.evidence_status
    fn.extra["summary_missing_context"] = list(summary_out.missing_context)
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
    fn.extra["summary_evidence_status"] = "insufficient"
    fn.extra["summary_missing_context"] = [reason]
    ctx.extra["summary_evidence_status"] = "insufficient"
    ctx.extra["summary_missing_context"] = [reason]


def _finalize_semantic_frame(
    fn: CodeNode,
    ctx: AnalysisContext,
    insights_out: object | None,
) -> None:
    plan = ctx.extra.get("_semantic_frame_plan")
    if plan is None:
        raise ValueError(f"semantic frame plan missing: {fn.id}")
    _profile, all_specs, _concerns = plan
    frames = [fn.extra.get("_summary_semantic_frame")]
    if insights_out is not None:
        frames.append(getattr(insights_out, "semantic_frame", None))
    merged = merge_semantic_frames(
        *frames,
        required_slot_ids=(spec.slot_id for spec in all_specs),
    )
    payload = merged.model_dump(mode="json", by_alias=True)
    fn.extra["semantic_frame_schema"] = merged.schema_version
    fn.extra["semantic_frame"] = payload
    ctx.extra["semantic_frame_schema"] = merged.schema_version
    ctx.extra["semantic_frame"] = payload


def _build_user_values(
    ctx: AnalysisContext,
    fn: CodeNode,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
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
    prompt_body = project_model_source_literals(body_for(ctx, contexts, cache))
    inventory = build_control_inventory(fn, ctx, cache)
    # 상태 추적 없이 소스에 직접 쓰인 반환 표현식만 bottom-up 인터페이스로 전달한다.
    ctx.extra["return_expressions"] = sorted(inventory.return_expressions)
    persist_branch_table(fn, inventory)
    persist_rule_audit(fn, inventory)
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
        "_external_evidence_by_chunk": {
            item.id: item.as_exact_prompt_dict()
            for item in ctx.external_evidence
        },
        "callee_summaries": _format_callee_summaries(ctx, contexts),
        "ancestor_context": ancestor_context(ctx, contexts, cache),
        "reads": dump_table_refs(ctx.reads, table_descriptions, include_columns=False),
        "writes": dump_table_refs(ctx.writes, table_descriptions, include_columns=False),
        "direct_reads": dump_table_refs(
            exact_direct_table_ids(ctx, "READ"),
            table_descriptions,
            include_columns=False,
        ),
        "direct_writes": dump_table_refs(
            exact_direct_table_ids(ctx, "WRITE"),
            table_descriptions,
            include_columns=False,
        ),
        "table_ops": json.dumps(
            ctx.extra.get("table_op") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "data_object_usages": dump_data_object_usages(
            ctx.extra.get("data_object_usages") or ()
        ),
        "column_usage_candidates": json.dumps(
            ctx.extra.get("column_usage_candidates") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "structural_profile": profile_prompt_json(profile, concerns["summary"]),
        "applicable_slots": slot_specs_json(concerns["summary"]),
        # spec 102: 규칙이 고를 수 있는 근거 분기 후보. 분할 3 템플릿만 참조한다.
        "branch_index": project_model_source_literals(inventory.branch_index()),
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
            items.append({
                "relation": "TARGET_CALLS_CALLEE",
                "callee_id": callee_id,
                "summary": text,
                "evidence_status": c.extra.get("summary_evidence_status") or "sufficient",
                "missing_context": list(c.extra.get("summary_missing_context") or ()),
            })
    return items


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
