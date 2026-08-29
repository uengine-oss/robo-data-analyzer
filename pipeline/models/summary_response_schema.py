"""Closed response schema for the one live Summary concern."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.languages import stereotype_values
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.models.semantic_frame import (
    SlotSpec,
    StructuralProfile,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
    stable_digest,
)
from pipeline.models.semantic_frame_projection import project_summary


def frame_summary_response_model(
    strategy: str,
    slot_specs: Iterable[SlotSpec],
) -> type[BaseModel]:
    """Build the exact schema for the target's single Summary response."""
    specs = tuple(slot_specs)
    slot_model = semantic_frame_response_model(specs)
    slots_type = slot_model.model_fields["slots"].annotation
    stereotype_type = Literal.__getitem__(tuple(stereotype_values(strategy)))
    fingerprint = stable_digest({
        "strategy": strategy,
        "slots": [spec.model_dump(mode="json") for spec in specs],
        "concern": "frame-summary",
    })
    # Provider-visible property order is intentional: grounded details are emitted
    # before the whole-target synthesis that uses them.
    fields = {
        "slots": (slots_type, Field(...)),
        "summary": (
            str,
            Field(..., description=SummaryOutput.model_fields["summary"].description),
        ),
        "stereotype": (stereotype_type, Field(...)),
    }
    model = create_model(
        f"AssignedFrameSummary_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    model.__semantic_slot_bindings__ = slot_model.__semantic_slot_bindings__
    return model


def normalize_frame_summary_response(
    output: BaseModel,
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
) -> SummaryOutput:
    """Validate a target-owner response and attach its canonical semantic frame."""
    frame = normalize_semantic_frame_response(output, profile, slot_specs)
    return project_summary(
        frame,
        summary=str(output.summary),
        stereotype=str(output.stereotype),
    )
