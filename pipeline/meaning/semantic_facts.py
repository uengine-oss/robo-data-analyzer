"""Semantic frame evidence와 slot의 충돌 방지 생성기."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pipeline.models.semantic_frame import EvidenceFact, SlotSpec, evidence_id, slot_id


def make_fact(
    *,
    target_id: str,
    kind: str,
    identity: object,
    owner_id: str,
    file: str = "",
    start_line: int = 0,
    end_line: int = 0,
    attributes: Mapping[str, Any] | None = None,
) -> EvidenceFact:
    return EvidenceFact(
        id=evidence_id(target_id, kind, identity),
        kind=kind,
        owner_id=owner_id,
        file=file,
        start_line=start_line,
        end_line=end_line,
        attributes=dict(attributes or {}),
    )


def add_fact(evidence: dict[str, EvidenceFact], fact: EvidenceFact) -> str:
    existing = evidence.get(fact.id)
    if existing is not None and existing != fact:
        raise ValueError(f"semantic evidence id collision: {fact.id}")
    evidence[fact.id] = fact
    return fact.id


def add_slot(
    slots: dict[str, SlotSpec],
    *,
    target_id: str,
    family: str,
    role: str,
    anchor: object,
    target_ref: str,
    evidence_refs: Iterable[str],
) -> None:
    refs = tuple(dict.fromkeys(str(ref) for ref in evidence_refs if str(ref)))
    if not refs:
        raise ValueError(f"semantic slot requires evidence: {family}.{role}")
    sid = slot_id(target_id, family, role, anchor)
    spec = SlotSpec(
        slot_id=sid,
        family=family,
        role=role,
        target_ref=target_ref,
        evidence_refs=refs,
    )
    existing = slots.get(sid)
    if existing is not None and existing != spec:
        raise ValueError(f"semantic slot id collision: {sid}")
    slots[sid] = spec
