"""Meaning-only LLM boundary for access-specific function-to-table usage.

The Analyzer groups exact DATA_OBJECT occurrences into ``(function, table,
access)`` assignments before the call. The model returns one meaning string per
request-local ``FT#`` key and never echoes structural coordinates.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.models.code_analysis_output import (
    BatchFunctionTableUsagesItem,
    BatchFunctionTableUsagesOutput,
    FunctionTableUsagesOutput,
)
from pipeline.models.function_table_usage import (
    FunctionTableUsageSlotGroup,
    group_function_table_usage_slots,
    project_function_table_usages,
)
from pipeline.models.semantic_frame import (
    SemanticFrame,
    SemanticSlot,
    SlotSpec,
    StructuralProfile,
    stable_digest,
)


def _usage_groups(
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
    reads: Iterable[str],
    writes: Iterable[str],
) -> tuple[FunctionTableUsageSlotGroup, ...]:
    return group_function_table_usage_slots(
        profile,
        tuple(slot_specs),
        direct_reads=reads,
        direct_writes=writes,
    )


def function_table_usage_prompt_assignments(
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
    reads: Iterable[str],
    writes: Iterable[str],
) -> dict[str, dict[str, object]]:
    """Return one self-contained prompt assignment per direct table access."""
    specs = tuple(slot_specs)
    result: dict[str, dict[str, object]] = {}
    for ordinal, group in enumerate(
        _usage_groups(profile, specs, reads, writes), start=1,
    ):
        result[f"FT{ordinal}"] = {
            "physical_table_id": group.physical_table_id,
            "access_mode": group.access_mode,
            "occurrences": [
                {
                    "source_line": fact.start_line,
                    "operation": fact.attributes.get("operation"),
                    "qualified_columns": fact.attributes.get("qualified_columns") or [],
                }
                for fact in group.facts
            ],
        }
    return result


def function_table_usages_response_model(
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
    reads: Iterable[str],
    writes: Iterable[str],
) -> type[BaseModel]:
    """Require every Analyzer-assigned FT key exactly once and nothing else."""
    assignments = function_table_usage_prompt_assignments(
        profile, tuple(slot_specs), reads, writes,
    )
    fingerprint = stable_digest(assignments)
    return create_model(
        f"AssignedFunctionTableUsageMeanings_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **{
            local_key: (str, Field(...))
            for local_key in assignments
        },
    )


def _mentions_private_key(text: str, keys: Iterable[str]) -> bool:
    return any(re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", text,
    ) for key in keys)


def normalize_function_table_usages_response(
    output: BaseModel,
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
    reads: Iterable[str],
    writes: Iterable[str],
) -> FunctionTableUsagesOutput:
    """Join request-local FT branches to Analyzer-owned occurrence slots."""
    specs = tuple(slot_specs)
    groups = _usage_groups(profile, specs, reads, writes)
    assignments = function_table_usage_prompt_assignments(
        profile, specs, reads, writes,
    )
    private_keys = tuple(assignments)
    slots: dict[str, SemanticSlot] = {}
    for ordinal, group in enumerate(groups, start=1):
        local_key = f"FT{ordinal}"
        meaning = " ".join(str(getattr(output, local_key)).split())
        if _mentions_private_key(meaning, private_keys):
            raise ValueError(
                "function-table usage meaning leaked a request-local key: "
                f"{local_key}"
            )
        if meaning:
            status = "sufficient"
            missing_context: tuple[str, ...] = ()
        else:
            status = "insufficient"
            missing_context = (
                "function-table usage meaning is not derivable from available input",
            )
        for slot in group.slots:
            slots[slot.slot_id] = SemanticSlot(
                slot_id=slot.slot_id,
                family=slot.family,
                role=slot.role,
                target_ref=slot.target_ref,
                evidence_refs=slot.evidence_refs,
                meaning=meaning,
                status=status,
                missing_context=missing_context,
            )
    frame = SemanticFrame(target=profile.target, profile=profile, slots=slots)
    return FunctionTableUsagesOutput(
        function_table_usages=list(project_function_table_usages(
            frame,
            direct_reads=reads,
            direct_writes=writes,
        )),
        semantic_frame=frame,
    )


def batch_function_table_usages_response_model(
    assignments: Mapping[str, Mapping[str, object]],
) -> type[BaseModel]:
    """Require every assigned DBMS node and every FT branch exactly once."""
    if not assignments:
        raise ValueError("function-table usage assignments must not be empty")
    fingerprint = stable_digest({
        key: function_table_usage_prompt_assignments(
            assignment["profile"],
            assignment["slot_specs"],
            assignment.get("reads", ()),
            assignment.get("writes", ()),
        )
        for key, assignment in assignments.items()
    })
    node_fields = {}
    for node_key, assignment in assignments.items():
        node_key = str(node_key)
        if not node_key:
            raise ValueError("function-table usage node key must not be empty")
        item_model = function_table_usages_response_model(
            assignment["profile"],
            assignment["slot_specs"],
            assignment.get("reads", ()),
            assignment.get("writes", ()),
        )
        node_fields[node_key] = (item_model, Field(...))
    nodes_model = create_model(
        f"AssignedBatchFunctionTableUsageMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **node_fields,
    )
    return create_model(
        f"AssignedBatchFunctionTableUsages_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        nodes=(nodes_model, Field(...)),
    )


def normalize_batch_function_table_usages_response(
    output: BaseModel,
    assignments: Mapping[str, Mapping[str, object]],
) -> BatchFunctionTableUsagesOutput:
    nodes: list[BatchFunctionTableUsagesItem] = []
    for node_key, assignment in assignments.items():
        normalized = normalize_function_table_usages_response(
            getattr(output.nodes, node_key),
            assignment["profile"],
            assignment["slot_specs"],
            assignment.get("reads", ()),
            assignment.get("writes", ()),
        )
        nodes.append(BatchFunctionTableUsagesItem(
            node_key=node_key,
            function_table_usages=normalized.function_table_usages,
            semantic_frame=normalized.semantic_frame,
        ))
    return BatchFunctionTableUsagesOutput(nodes=nodes)
