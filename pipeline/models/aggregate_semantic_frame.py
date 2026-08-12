"""Bounded semantic frames for module/component aggregate targets."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SlotSpec,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
    slot_id,
)


def build_aggregate_semantic_plan(
    *,
    target_id: str,
    target_type: str,
    owner_id: str,
    file: str,
    start_line: int,
    end_line: int,
    family: Literal["MODULE", "COMPONENT"],
    member_frames: Iterable[SemanticFrame],
    include_responsibility: bool,
    roles: tuple[str, ...] | None = None,
    comment_ref: str = "",
) -> tuple[StructuralProfile, tuple[SlotSpec, ...]]:
    frames = tuple(member_frames)
    member_slot_refs = tuple(dict.fromkeys(
        slot_id_ for frame in frames for slot_id_ in frame.slots
    ))
    target = StructuralTarget(
        id=target_id,
        type=target_type,
        owner_id=owner_id,
        file=file or "(derived)",
        start_line=start_line,
        end_line=end_line,
    )
    target_ref = evidence_id(target_id, "TARGET", 0)
    member_ref = evidence_id(target_id, "MODULE", {"members": member_slot_refs})
    evidence = {
        target_ref: EvidenceFact(
            id=target_ref,
            kind="TARGET",
            owner_id=target_id,
            file=target.file,
            start_line=start_line,
            end_line=end_line,
            attributes={
                "type": target_type,
                "signature": "",
                "name": target_id,
                "params": "",
                "return_type": "",
            },
        ),
        member_ref: EvidenceFact(
            id=member_ref,
            kind="MODULE",
            owner_id=target_id,
            attributes={
                "module_id": target_id,
                "type": target_type,
                "member_refs": list(member_slot_refs),
                "comment_ref": comment_ref,
            },
        ),
    }
    profile = StructuralProfile(target=target, evidence=evidence)
    selected_roles = list(roles or ("DISPLAY_NAME",))
    if include_responsibility and "RESPONSIBILITY" not in selected_roles:
        selected_roles.insert(0, "RESPONSIBILITY")
    specs = tuple(
        SlotSpec(
            slot_id=slot_id(target_id, family, role, "TARGET"),
            family=family,
            role=role,
            target_ref=target_ref,
            evidence_refs=(target_ref, member_ref),
        )
        for role in selected_roles
    )
    return profile, specs


def aggregate_response_model(
    name: str,
    specs: Iterable[SlotSpec],
    *,
    stereotype_values: tuple[str, ...] | None = None,
) -> type[BaseModel]:
    frame_wire = semantic_frame_response_model(specs)
    fields: dict[str, tuple[object, object]] = {
        "slots": (frame_wire.model_fields["slots"].annotation, Field(...)),
    }
    if stereotype_values is not None:
        fields["stereotype"] = (
            Literal.__getitem__(stereotype_values),
            Field(...),
        )
    return create_model(
        name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def normalize_aggregate_response(
    output: BaseModel,
    profile: StructuralProfile,
    specs: Iterable[SlotSpec],
) -> SemanticFrame:
    return normalize_semantic_frame_response(output, profile, specs)


def aggregate_slot_meaning(frame: SemanticFrame, role: str) -> str:
    matches = [slot.meaning for slot in frame.slots.values() if slot.role == role]
    if len(matches) != 1:
        raise ValueError(f"aggregate frame requires one {role} slot")
    return matches[0]
