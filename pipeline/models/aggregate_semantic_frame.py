"""Bounded semantic frames for module/component aggregate targets."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SemanticSlot,
    SlotSpec,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    slot_id,
)


def _selected_member_slots(
    member_frames: Iterable[SemanticFrame],
    roles: tuple[str, ...] | None,
) -> tuple[SemanticSlot, ...]:
    frames = tuple(member_frames)
    if not frames:
        raise ValueError("aggregate projection requires member frames")
    role_order = (
        {role: index for index, role in enumerate(roles)}
        if roles is not None
        else None
    )
    selected: list[SemanticSlot] = []
    seen_slot_ids: set[str] = set()
    for frame in frames:
        frame_slots = sorted(
            (
                slot for slot in frame.slots.values()
                if role_order is None or slot.role in role_order
            ),
            key=(
                (lambda item: item.slot_id)
                if role_order is None
                else (lambda item: (role_order[item.role], item.slot_id))
            ),
        )
        if not frame_slots:
            raise ValueError(
                "aggregate member has no selected semantic slot: "
                f"target={frame.profile.target.id}"
            )
        for slot in frame_slots:
            if slot.slot_id in seen_slot_ids:
                raise ValueError(
                    "aggregate member slot must occur exactly once: "
                    f"slot_id={slot.slot_id}"
                )
            seen_slot_ids.add(slot.slot_id)
            selected.append(slot)
    return tuple(selected)


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
    member_slots = _selected_member_slots(frames, member_roles)
    member_slot_refs = tuple(slot.slot_id for slot in member_slots)
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


def project_aggregate_frame(
    profile: StructuralProfile,
    specs: Iterable[SlotSpec],
    member_frames: Iterable[SemanticFrame],
    *,
    member_roles: tuple[str, ...],
    physical_name: str,
) -> SemanticFrame:
    """Project member claims exactly once without another semantic LLM.

    Member meanings remain canonical leaves owned by their source-grounded frames.  The
    aggregate responsibility is a deterministic, ordered concatenation; it does not
    paraphrase, deduplicate or choose a Top-N subset.  A physical module/component name
    may be retained as an explicitly partial display value, never promoted to an inferred
    domain name.
    """
    selected = _selected_member_slots(member_frames, member_roles)
    if not selected:
        raise ValueError("aggregate projection requires selected member slots")
    member_fact = next(
        (fact for fact in profile.evidence.values() if fact.kind == "MODULE"),
        None,
    )
    if member_fact is None:
        raise ValueError("aggregate profile has no member evidence")
    expected_refs = tuple(member_fact.attributes.get("member_refs") or ())
    actual_refs = tuple(slot.slot_id for slot in selected)
    if actual_refs != expected_refs:
        raise ValueError(
            "aggregate member projection differs from canonical member refs"
        )

    slots: dict[str, SemanticSlot] = {}
    for spec in specs:
        if spec.role == "RESPONSIBILITY":
            meanings = [slot.meaning for slot in selected if slot.meaning]
            missing: list[str] = []
            for slot in selected:
                if slot.status == "sufficient" and slot.meaning:
                    continue
                reasons = slot.missing_context or (
                    "member semantic meaning is unavailable",
                )
                missing.extend(
                    f"member slot {slot.slot_id}: {reason}" for reason in reasons
                )
            meaning = "\n".join(meanings)
            status = (
                "sufficient" if meaning and not missing
                else "partial" if meaning
                else "insufficient"
            )
            missing_context = tuple(missing)
        elif spec.role == "DISPLAY_NAME":
            meaning = str(physical_name or "").strip()
            status = "partial" if meaning else "insufficient"
            missing_context = (
                "source-owned aggregate display meaning unavailable; "
                "physical identifier preserved only",
            )
        else:
            raise ValueError(f"unsupported deterministic aggregate role: {spec.role}")
        slots[spec.slot_id] = SemanticSlot(
            slot_id=spec.slot_id,
            family=spec.family,
            role=spec.role,
            target_ref=spec.target_ref,
            evidence_refs=spec.evidence_refs,
            meaning=meaning,
            status=status,
            missing_context=missing_context,
        )
    return SemanticFrame(target=profile.target, profile=profile, slots=slots)


def sealed_summary_frame_payload(node: object) -> object | None:
    """Return only the immutable summary handoff owned by aggregate consumers."""
    extra = getattr(node, "extra", None) or {}
    return extra.get("_summary_semantic_frame")


def aggregate_slot_meaning(frame: SemanticFrame, role: str) -> str:
    matches = [slot.meaning for slot in frame.slots.values() if slot.role == role]
    if len(matches) != 1:
        raise ValueError(f"aggregate frame requires one {role} slot")
    return matches[0]
