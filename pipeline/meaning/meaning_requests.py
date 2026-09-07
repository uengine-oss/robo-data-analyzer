"""Summary와 Rule 의미 요청의 입력 조립, 스케줄링, 응답 투영."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Awaitable, Callable

from llm.runners.model_output_error import ModelOutputError
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.usage.cost_budget import CostBudget
from pipeline.execution.analysis_handoff import (
    RULES_LANE,
    SUMMARY_LANE,
    SUMMARY_WAKE_PRIORITY,
)
from pipeline.meaning.prompt_assets import semantic_prompt_dir
from pipeline.meaning.rule.meaning_frame import (
    build_rule_meaning_packet,
    configured_rule_effect_limit,
    partition_rule_meaning_evidence,
    prepare_rule_meaning_assignment,
    project_rule_meanings,
    rule_meaning_response_model,
    unavailable_rule_meaning_projections,
)
from pipeline.meaning.rule.row_batching import apply_rule_meaning_projections
from pipeline.meaning.summary.response_schema import (
    frame_summary_response_model,
    normalize_frame_summary_response,
)
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import SummaryOutput
from shared.concurrency.priority_semaphore import PrioritySemaphore
from shared.events.processing_events import track_processing
from shared.events.runtime_logger import log_info

_STEP = "step7.analyze_code.meanings"


def _assigned_external_source_texts(
    profile,
    slot_specs,
    external_by_chunk: dict,
) -> list[dict[str, str]]:
    """Return self-contained linked external text without exposing fact identities."""
    specs = tuple(slot_specs)
    if not specs:
        return []
    assigned_refs = {
        ref
        for spec in specs
        for ref in (spec.target_ref, *spec.evidence_refs)
    }
    source_texts: list[dict[str, str]] = []
    ordered_refs = sorted(
        assigned_refs,
        key=lambda ref: (
            int(profile.evidence[ref].start_line),
            int(profile.evidence[ref].end_line),
            profile.evidence[ref].kind,
            ref,
        ),
    )
    for ref in ordered_refs:
        fact = profile.evidence[ref]
        if fact.kind != "EXTERNAL":
            continue
        chunk_id = str(fact.attributes.get("chunk_id") or "")
        source_text = external_by_chunk.get(chunk_id)
        if source_text is None:
            raise ValueError(
                f"assigned linked external source is missing: {chunk_id or fact.id}"
            )
        source_texts.append({
            "kind": str(fact.attributes.get("semantic_kind") or ""),
            "matched_key": str(fact.attributes.get("matched_key") or ""),
            "text": str(source_text),
        })
    return source_texts


def direct_callee_effects_by_id(
    ctx: AnalysisContext,
    contexts: dict[str, AnalysisContext],
) -> dict[str, dict[str, object]]:
    """Return only deterministic caller-visible effects absent from caller source."""
    outcomes: dict[str, dict[str, object]] = {}
    for callee_id in ctx.callees:
        callee = contexts.get(callee_id)
        if callee is None:
            continue
        signature = (callee.signature or "").strip()
        if not signature:
            raise ValueError("linked callee effect lacks a model-safe signature")
        value: dict[str, object] = {"callee": signature}
        returns = tuple(callee.extra.get("return_expressions") or ())
        if returns:
            value["returns"] = list(returns)
        if callee.writes:
            value["writes"] = list(callee.writes)
        terminations = tuple(callee.extra.get("termination_effects") or ())
        if terminations:
            value["terminations"] = list(terminations)
        if len(value) > 1:
            outcomes[callee_id] = value
    return outcomes


def summary_prompt_values(profile, slot_specs, analysis_inputs: dict) -> dict:
    """Build the minimal Summary packet without repeating source expressions."""
    specs = tuple(slot_specs)
    source_text = str(analysis_inputs.get("raw_source_code") or "")
    external_by_chunk = dict(analysis_inputs.get("external_source_text_by_id") or {})
    source_lines = source_text.split("\n") if source_text else []
    target_start = max(int(profile.target.start_line), 1)
    source_packet = "\n".join(
        f"{target_start + index} | {source_lines[index]}"
        for index in range(len(source_lines))
    )
    external_source_texts = _assigned_external_source_texts(
        profile, specs, external_by_chunk,
    )
    linked_callee_effects = list(
        dict(analysis_inputs.get("callee_effects_by_id") or {}).values()
    )

    def _dump(value: object) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    outline = str(analysis_inputs.get("source_outline") or "").strip()
    if not outline:
        span = (
            str(profile.target.start_line)
            if profile.target.start_line == profile.target.end_line
            else f"{profile.target.start_line}-{profile.target.end_line}"
        )
        outline = f"{span} : {profile.target.type}"
    sections = ["## [STRUCTURE — line range : syntax type]\n" + outline]
    for title, value in (
        ("DIRECT TABLE ACCESS", analysis_inputs.get("direct_table_usages") or ()),
        ("EXTERNAL SYMBOLS", analysis_inputs.get("referenced_symbols") or ()),
        ("DIRECT CALLEE EFFECTS", linked_callee_effects),
        ("LINKED PROJECT CONTRACTS", external_source_texts),
    ):
        if value:
            sections.append(f"## [{title}]\n{_dump(value)}")
    return {
        "target_id": profile.target.id,
        "code_body": source_packet,
        "summary_context": "\n\n".join(sections),
    }


def _summary_input_counts(profile, slot_specs, analysis_inputs: dict) -> dict[str, int]:
    source = str(analysis_inputs.get("raw_source_code") or "")
    outline = str(analysis_inputs.get("source_outline") or "").strip()
    external_sources = _assigned_external_source_texts(
        profile,
        tuple(slot_specs),
        dict(analysis_inputs.get("external_source_text_by_id") or {}),
    )
    return {
        "source_lines": len(source.split("\n")) if source else 0,
        "structure_nodes": len(outline.splitlines()) if outline else 1,
        "direct_table_usages": len(analysis_inputs.get("direct_table_usages") or ()),
        "external_symbols": len(analysis_inputs.get("referenced_symbols") or ()),
        "direct_callee_effects": len(analysis_inputs.get("callee_effects_by_id") or {}),
        "linked_project_contracts": len(external_sources),
    }


def _rule_input_counts(packet: dict[str, object]) -> dict[str, int]:
    return {
        "conditions": len(packet.get("conditions") or {}),
        "effects": len(packet.get("effects") or {}),
        "rows": len(packet.get("rows") or {}),
        "used_symbols": len(packet.get("used_symbols") or ()),
    }


async def run_frame_summary(
    *, profile, slot_specs, strategy_name: str, analysis_inputs: dict, invoke,
) -> SummaryOutput:
    specs = tuple(slot_specs)
    values = summary_prompt_values(profile, specs, analysis_inputs)
    output = await invoke(values, frame_summary_response_model(strategy_name, specs))
    return normalize_frame_summary_response(output, profile, specs)


class MeaningRequestRunner:
    """Run-scoped request boundary shared by one node's Summary and Rule work."""

    def __init__(
        self,
        *,
        code_id: str,
        strategy_name: str,
        llm_slots: PrioritySemaphore,
        cost_budget: CostBudget | None,
    ) -> None:
        self.code_id = code_id
        self.strategy_name = strategy_name
        self.llm_slots = llm_slots
        self.cost_budget = cost_budget
        self.summary_prompts_dir = semantic_prompt_dir(
            family="summary", strategy=strategy_name,
        )
        self.rule_prompts_dir = semantic_prompt_dir(
            family="rule", strategy=strategy_name,
        )

    async def _track(
        self,
        sub_action: str,
        invoke: Callable[[dict[str, object]], Awaitable[object]],
        *,
        priority: int = 0,
        lane: str,
        payload_chars: int = 0,
        input_sections: dict[str, int] | None = None,
        input_counts: dict[str, int] | None = None,
    ):
        async with track_processing("CodeNode", self.code_id, sub_action=sub_action):
            queued_at = time.monotonic()
            waiting_before = self.llm_slots.waiting
            async with self.llm_slots.at(priority, lane):
                queue_wait_ms = round((time.monotonic() - queued_at) * 1000, 3)
                log_info(
                    _STEP,
                    "llm_slot_acquired",
                    code_id=self.code_id,
                    concern=sub_action,
                    lane=lane,
                    priority=priority,
                    payload_chars=payload_chars,
                    queue_wait_ms=queue_wait_ms,
                    waiting_before=waiting_before,
                    slots_in_use=self.llm_slots.in_use,
                    lane_slots_in_use=self.llm_slots.in_use_for(lane),
                    slots_capacity=self.llm_slots.capacity,
                )
                return await invoke({
                    "concern": sub_action,
                    "lane": lane,
                    "queue_wait_ms": queue_wait_ms,
                    "payload_chars": payload_chars,
                    "input_sections": dict(input_sections or {}),
                    "input_counts": dict(input_counts or {}),
                    "waiting_before": waiting_before,
                    "slots_in_use": self.llm_slots.in_use,
                    "lane_slots_in_use": self.llm_slots.in_use_for(lane),
                    "slots_capacity": self.llm_slots.capacity,
                })

    async def summary(self, *, profile, slot_specs, analysis_inputs: dict) -> SummaryOutput:
        async def _invoke(values, response_model):
            return await self._track(
                "split_summary",
                lambda request_metrics: run_yaml_rule(
                    prompts_dir=self.summary_prompts_dir,
                    rule_name="code_summary",
                    user_values=values,
                    response_model=response_model,
                    budget=self.cost_budget,
                    request_metrics=request_metrics,
                ),
                priority=SUMMARY_WAKE_PRIORITY,
                lane=SUMMARY_LANE,
                payload_chars=sum(len(str(value)) for value in values.values()),
                input_sections={
                    "source": len(values["code_body"]),
                    "context": len(values["summary_context"]),
                },
                input_counts=_summary_input_counts(profile, slot_specs, analysis_inputs),
            )

        return await run_frame_summary(
            profile=profile,
            slot_specs=slot_specs,
            strategy_name=self.strategy_name,
            analysis_inputs=analysis_inputs,
            invoke=_invoke,
        )

    async def rules(
        self,
        *,
        collection,
        ctx: AnalysisContext,
        contexts: dict[str, AnalysisContext],
        table_descriptions: dict[str, dict],
    ):
        if not collection.rules:
            return collection
        assignment = prepare_rule_meaning_assignment(collection.evidence)
        model_evidence = assignment.model_evidence
        conditions = model_evidence.get("conditions") or {}
        effects = model_evidence.get("effects") or {}
        rows = model_evidence.get("rows") or {}
        if not rows:
            return apply_rule_meaning_projections(
                collection,
                assignment.deterministic_projections,
                target_scope=self.code_id,
                count_call=False,
            )
        if not conditions and not effects:
            return apply_rule_meaning_projections(
                collection,
                (
                    *unavailable_rule_meaning_projections(
                        model_evidence,
                        reason=(
                            "Rule row has no condition/effect atom from which to derive "
                            "business meaning"
                        ),
                    ),
                    *assignment.deterministic_projections,
                ),
                target_scope=self.code_id,
                count_call=False,
            )
        partitions = partition_rule_meaning_evidence(
            model_evidence,
            max_effects=configured_rule_effect_limit(),
        )
        results = await asyncio.gather(*(
            self._rule_partition(
                partition=partition,
                index=index,
                partition_count=len(partitions),
                ctx=ctx,
                contexts=contexts,
                table_descriptions=table_descriptions,
            )
            for index, partition in enumerate(partitions, start=1)
        ))
        return apply_rule_meaning_projections(
            collection,
            (
                *(projection for result in results for projection in result),
                *assignment.deterministic_projections,
            ),
            target_scope=self.code_id,
            count_call=len(partitions),
        )

    async def _rule_partition(
        self,
        *,
        partition: dict[str, object],
        index: int,
        partition_count: int,
        ctx: AnalysisContext,
        contexts: dict[str, AnalysisContext],
        table_descriptions: dict[str, dict],
    ):
        packet = build_rule_meaning_packet(
            partition,
            referenced_symbols=ctx.referenced_symbols,
            callee_effects_by_id=direct_callee_effects_by_id(ctx, contexts),
            table_descriptions=table_descriptions,
            api_contracts=tuple(ctx.extra.get("api_contracts") or ()),
        )
        values = {
            "target_id": self.code_id,
            "rule_packet": json.dumps(
                packet,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        counts = _rule_input_counts(packet)
        counts.update({"partition_index": index, "partition_count": partition_count})
        try:
            output = await self._track(
                "split_rules",
                lambda request_metrics: run_yaml_rule(
                    prompts_dir=self.rule_prompts_dir,
                    rule_name="code_rule_meanings",
                    user_values=values,
                    response_model=rule_meaning_response_model(
                        packet.get("conditions", {}),
                        packet["effects"],
                        packet["rows"],
                    ),
                    budget=self.cost_budget,
                    request_metrics=request_metrics,
                ),
                lane=RULES_LANE,
                payload_chars=len(values["rule_packet"]),
                input_sections={"rule_packet": len(values["rule_packet"])},
                input_counts=counts,
            )
        except ModelOutputError as error:
            return unavailable_rule_meaning_projections(partition, reason=str(error))
        return project_rule_meanings(output, partition)
