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
    member_roles: tuple[str, ...] | None = None,
    roles: tuple[str, ...] | None = None,
    comment_ref: str = "",
) -> tuple[StructuralProfile, tuple[SlotSpec, ...]]:
    frames = tuple(member_frames)
    selected_roles = set(member_roles or ())
    member_slot_refs = tuple(dict.fromkeys(
        slot_id_
        for frame in frames
        for slot_id_, slot in frame.slots.items()
        if not selected_roles or slot.role in selected_roles
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


def aggregate_member_payloads(
    member_frames: Iterable[SemanticFrame],
    *,
    roles: tuple[str, ...],
) -> list[dict[str, object]]:
    """Project validated member meaning without copying source/profile/rule evidence.

    Aggregate targets consume the canonical summary handoff, not a member's full merged
    semantic frame.  The child slot already owns and validates its source lineage, so the
    parent needs the slot identity and accepted meaning only.  Copying the entire child
    profile made aggregate input depend on whether Rule work happened to finish before
    the summary handoff and expanded one 34-routine component request to 272k tokens.
    """
    allowed = set(roles)
    if not allowed:
        raise ValueError("aggregate member projection requires at least one role")
    payloads: list[dict[str, object]] = []
    for frame in member_frames:
        slots = [
            {
                "slot_id": slot.slot_id,
                "family": slot.family,
                "role": slot.role,
                "meaning": slot.meaning,
                "status": slot.status,
                "missing_context": list(slot.missing_context),
            }
            for slot in frame.slots.values()
            if slot.role in allowed
        ]
        if not slots:
            raise ValueError(
                "aggregate member summary has no selected semantic slot: "
                f"target={frame.profile.target.id}, roles={sorted(allowed)}"
            )
        payloads.append({
            "target_id": frame.profile.target.id,
            "slots": slots,
        })
    return payloads


def sealed_summary_frame_payload(node: object) -> object | None:
    """Return only the immutable summary handoff owned by aggregate consumers."""
    extra = getattr(node, "extra", None) or {}
    return extra.get("_summary_semantic_frame")


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
