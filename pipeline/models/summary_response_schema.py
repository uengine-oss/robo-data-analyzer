"""Closed wire models for the model-owned semantic header."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.languages import stereotype_values
from pipeline.models.code_analysis_output import (
    BatchSummaryItem,
    BatchSummaryOutput,
    QuestionItem,
    SummaryOutput,
)
from pipeline.models.semantic_frame import (
    SlotSpec,
    StructuralProfile,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
    stable_digest,
)
from pipeline.models.semantic_frame_projection import project_summary


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _wire_item_type(strategy: str, fingerprint: str) -> object:
    """Encode the cross-field evidence contract in provider-visible JSON Schema."""
    stereotype_type = Literal.__getitem__(tuple(stereotype_values(strategy)))
    common = {
        "questions": (
            list[QuestionItem],
            Field(..., max_length=15, description=SummaryOutput.model_fields["questions"].description),
        ),
    }
    sufficient = create_model(
        f"SufficientSemanticHeader_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        summary=(str, Field(..., min_length=1, description=SummaryOutput.model_fields["summary"].description)),
        stereotype=(stereotype_type, Field(..., description=SummaryOutput.model_fields["stereotype"].description)),
        evidence_status=(Literal["sufficient"], Field(...)),
        missing_context=(list[str], Field(..., max_length=0)),
        **common,
    )
    partial = create_model(
        f"PartialSemanticHeader_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        summary=(str, Field(..., min_length=1, description=SummaryOutput.model_fields["summary"].description)),
        stereotype=(stereotype_type, Field(..., description=SummaryOutput.model_fields["stereotype"].description)),
        evidence_status=(Literal["partial"], Field(...)),
        missing_context=(list[str], Field(..., min_length=1, max_length=8)),
        **common,
    )
    insufficient = create_model(
        f"InsufficientSemanticHeader_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        summary=(Literal[""], Field(..., description=SummaryOutput.model_fields["summary"].description)),
        stereotype=(Literal[""], Field(..., description=SummaryOutput.model_fields["stereotype"].description)),
        evidence_status=(Literal["insufficient"], Field(...)),
        missing_context=(list[str], Field(..., min_length=1, max_length=8)),
        **common,
    )
    return Annotated[Union[sufficient, partial, insufficient], Field(discriminator="evidence_status")]


def summary_response_model(strategy: str) -> type[BaseModel]:
    fingerprint = _fingerprint({"strategy": strategy, "fields": "semantic-header"})
    return create_model(
        f"ClosedSemanticHeader_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        result=(_wire_item_type(strategy, fingerprint), Field(...)),
    )


def normalize_summary_response(output: BaseModel) -> SummaryOutput:
    values = output.result.model_dump()
    return SummaryOutput(
        summary=values["summary"],
        stereotype=values["stereotype"],
        evidence_status=values["evidence_status"],
        missing_context=values["missing_context"],
        questions=values["questions"],
    )


def batch_summary_response_model(node_keys: Iterable[str], strategy: str) -> type[BaseModel]:
    keys = tuple(str(key) for key in node_keys)
    if not keys or any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("DBMS summary node keys must be non-empty and unique")
    fingerprint = _fingerprint({
        "strategy": strategy,
        "node_keys": keys,
        "fields": "semantic-header",
    })
    item_model = _wire_item_type(strategy, fingerprint)
    node_fields = {
        node_key: (item_model, Field(...))
        for node_key in keys
    }
    nodes_model = create_model(
        f"ClosedBatchSemanticHeaderMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **node_fields,
    )
    return create_model(
        f"ClosedBatchSemanticHeader_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        nodes=(nodes_model, Field(...)),
    )


def normalize_batch_summary_response(
    output: BaseModel, node_keys: Iterable[str],
) -> BatchSummaryOutput:
    nodes = []
    for key in node_keys:
        values = getattr(output.nodes, str(key)).model_dump()
        nodes.append(BatchSummaryItem(
            node_key=str(key),
            summary=values["summary"],
            stereotype=values["stereotype"],
            evidence_status=values["evidence_status"],
            missing_context=values["missing_context"],
            questions=values["questions"],
        ))
    return BatchSummaryOutput(nodes=nodes)


def frame_summary_response_model(
    strategy: str,
    slot_specs: Iterable[SlotSpec],
) -> type[BaseModel]:
    """Closed summary concern: assigned slot leaves plus a closed role classification."""
    specs = tuple(slot_specs)
    slot_model = semantic_frame_response_model(specs)
    slots_type = slot_model.model_fields["slots"].annotation
    stereotype_type = Literal.__getitem__(tuple(stereotype_values(strategy)))
    fingerprint = stable_digest({
        "strategy": strategy,
        "slots": [spec.model_dump(mode="json") for spec in specs],
        "concern": "frame-summary",
    })
    return create_model(
        f"AssignedFrameSummary_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        slots=(slots_type, Field(...)),
        stereotype=(stereotype_type, Field(...)),
    )


def normalize_frame_summary_response(
    output: BaseModel,
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
) -> SummaryOutput:
    frame = normalize_semantic_frame_response(output, profile, slot_specs)
    return project_summary(frame, stereotype=str(output.stereotype))


def batch_frame_summary_response_model(
    strategy: str,
    assignments: Mapping[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> type[BaseModel]:
    if not assignments:
        raise ValueError("frame summary batch assignments must not be empty")
    stereotype_type = Literal.__getitem__(tuple(stereotype_values(strategy)))
    fingerprint = stable_digest({
        "strategy": strategy,
        "assignments": {
            key: [spec.model_dump(mode="json") for spec in specs]
            for key, (_profile, specs) in assignments.items()
        },
    })
    fields = {}
    for node_key, (_profile, specs) in assignments.items():
        slot_model = semantic_frame_response_model(specs)
        slots_type = slot_model.model_fields["slots"].annotation
        item_model = create_model(
            f"AssignedFrameSummaryItem_{fingerprint}_{stable_digest(node_key)}",
            __config__=ConfigDict(extra="forbid"),
            slots=(slots_type, Field(...)),
            stereotype=(stereotype_type, Field(...)),
        )
        fields[node_key] = (item_model, Field(...))
    nodes_model = create_model(
        f"AssignedFrameSummaryMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return create_model(
        f"AssignedBatchFrameSummary_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        nodes=(nodes_model, Field(...)),
    )


def normalize_batch_frame_summary_response(
    output: BaseModel,
    assignments: Mapping[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> BatchSummaryOutput:
    nodes: list[BatchSummaryItem] = []
    for node_key, (profile, specs) in assignments.items():
        item = getattr(output.nodes, node_key)
        projected = normalize_frame_summary_response(item, profile, specs)
        nodes.append(BatchSummaryItem(
            node_key=node_key,
            summary=projected.summary,
            stereotype=projected.stereotype,
            evidence_status=projected.evidence_status,
            missing_context=projected.missing_context,
            questions=projected.questions,
            semantic_frame=projected.semantic_frame,
        ))
    return BatchSummaryOutput(nodes=nodes)
