"""Closed response schema for the one live Summary concern."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field, create_model

from pipeline.languages import stereotype_values
from pipeline.models.code_analysis_output import SummaryOutput
from llm.structured_schema import CompactLlmResponse
from pipeline.models.semantic_frame import (
    SemanticFrame,
    SemanticSlot,
    SlotSpec,
    StructuralProfile,
)
from pipeline.models.semantic_frame_projection import project_summary


def frame_summary_response_model(
    strategy: str,
    slot_specs: Iterable[SlotSpec],
) -> type[BaseModel]:
    """Build the exact schema for the target's single Summary response."""
    specs = tuple(slot_specs)
    if len(specs) != 1:
        raise ValueError("Summary requires one whole-target semantic obligation")
    stereotype_type = Literal.__getitem__(tuple(stereotype_values(strategy)))
    fields = {
        "summary": (
            str,
            Field(..., description=SummaryOutput.model_fields["summary"].description),
        ),
        "stereotype": (stereotype_type, Field(...)),
    }
    model = create_model(
        "AssignedFrameSummary",
        __base__=CompactLlmResponse,
        **fields,
    )
    return model


def normalize_frame_summary_response(
    output: BaseModel,
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
) -> SummaryOutput:
    """Validate a target-owner response and attach its canonical semantic frame."""
    specs = tuple(slot_specs)
    if len(specs) != 1:
        raise ValueError("Summary requires one whole-target semantic obligation")
    spec = specs[0]
    summary = " ".join(str(output.summary).split())
    unavailable_callees = {
        ref
        for ref in spec.evidence_refs
        if (fact := profile.evidence[ref]).kind == "CALL"
        and fact.attributes.get("dependency_status") == "unavailable_cycle"
    }
    missing_context = (
        ("callee semantic dependency unavailable in call cycle",)
        if unavailable_callees else ()
    )
    if summary:
        status = "partial" if missing_context else "sufficient"
    else:
        status = "insufficient"
        missing_context = (
            *missing_context,
            "whole-target summary returned no meaning",
        )
    frame = SemanticFrame(
        target=profile.target,
        profile=profile,
        slots={
            spec.slot_id: SemanticSlot(
                slot_id=spec.slot_id,
                family=spec.family,
                role=spec.role,
                target_ref=spec.target_ref,
                evidence_refs=spec.evidence_refs,
                meaning=summary,
                status=status,
                missing_context=missing_context,
            ),
        },
    )
    return project_summary(
        frame,
        summary=summary,
        stereotype=str(output.stereotype),
    )
