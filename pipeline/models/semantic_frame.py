"""Canonical internal semantic-frame contracts.

Deterministic code owns target, evidence, obligation metadata and sufficiency.  Model
boundaries project only the concern-specific natural-language leaves they genuinely need;
canonical IDs and evidence bindings remain inside this ledger.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EvidenceStatus = Literal["sufficient", "partial", "insufficient"]
EvidenceKind = Literal[
    "TARGET", "CONTROL", "STATEMENT", "CALL", "DATA_OBJECT", "SYMBOL",
    "EXTERNAL", "EXPRESSION", "RULE", "TABLE", "COLUMN", "MODULE",
]
SlotFamily = Literal[
    "FUNCTION", "STATEMENT", "RULE", "DECISION", "LOOP", "EXCEPTION", "CALL",
    "SELECT", "INSERT", "UPDATE", "DELETE", "UPSERT", "TABLE", "COLUMN",
    "MODULE", "COMPONENT",
]


# Whole-function responsibility is owned once by the response's required ``summary``.
FUNCTION_SUMMARY_ROLES = (
    "WHOLE_FUNCTION",
)


_ALLOWED_EVIDENCE_ATTRIBUTES: dict[str, frozenset[str]] = {
    "TARGET": frozenset({
        "type", "signature", "name", "params", "return_type",
    }),
    "CONTROL": frozenset({
        "node_id", "type", "role", "condition", "condition_timing",
        "parent_ref", "complement_of", "implicit_false",
    }),
    "STATEMENT": frozenset({
        "node_id", "type", "expression", "target", "operator", "effects",
        "terminates",
    }),
    "CALL": frozenset({
        "callee_id", "callee_name", "recursive", "dependency_status",
        "occurrence_id", "callee_source",
        "callee_kind", "argument_sources", "receiver_source", "source_range",
        "structural_role", "condition_fact_id", "presence_status",
    }),
    "DATA_OBJECT": frozenset({
        "table_id", "access", "operation", "direct", "resolution",
        "qualified_columns",
    }),
    "SYMBOL": frozenset({
        "symbol_id", "name", "scope", "data_type", "declared_value",
        "value_semantics", "initialization_sources",
    }),
    "EXTERNAL": frozenset({
        "chunk_id", "semantic_kind", "provenance", "matched_key",
    }),
    "EXPRESSION": frozenset({
        "fact_id", "source_id", "code_node_id", "role", "source_range",
        "expression",
    }),
    "RULE": frozenset({
        "anchor_line", "role", "condition", "effects", "when", "source_refs",
        "type", "condition_timing", "complement_of", "implicit_false",
        "nested_control", "fallthrough_to", "at_label", "after_control",
    }),
    "TABLE": frozenset({
        "table_id", "db", "schema", "name", "source_description",
        "observed_operations", "usage_meanings",
    }),
    "COLUMN": frozenset({
        "table_id", "name", "source_description", "usage_evidence",
    }),
    "MODULE": frozenset({
        "module_id", "type", "member_refs", "comment_ref",
    }),
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_digest(value: object, *, length: int = 12) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def target_digest(target_id: str) -> str:
    if not str(target_id).strip():
        raise ValueError("target id must not be empty")
    return hashlib.sha256(str(target_id).encode("utf-8")).hexdigest()[:12]


def evidence_id(target_id: str, kind: str, identity: object) -> str:
    if kind not in _ALLOWED_EVIDENCE_ATTRIBUTES:
        raise ValueError(f"unsupported evidence kind: {kind!r}")
    digest = target_digest(target_id)
    if identity == 0 or identity == "0":
        anchor = "0"
    elif isinstance(identity, int) and identity > 0:
        anchor = str(identity)
    else:
        anchor = stable_digest(identity)
    return f"EV:{digest}:{kind}:{anchor}"


def slot_id(target_id: str, family: str, role: str, anchor: object) -> str:
    digest = target_digest(target_id)
    clean_family = str(family).strip().upper()
    clean_role = str(role).strip().upper()
    if not clean_family or not clean_role:
        raise ValueError("slot family and role must not be empty")
    if anchor == "TARGET":
        suffix = "TARGET"
    elif isinstance(anchor, int) and anchor > 0:
        suffix = str(anchor)
    else:
        suffix = stable_digest(anchor)
    return f"SL:{digest}:{clean_family}:{clean_role}:{suffix}"


class StructuralTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    owner_id: str = ""
    file: str = Field(..., min_length=1)
    start_line: int = Field(..., ge=0)
    end_line: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _coordinates(self) -> "StructuralTarget":
        if (self.start_line == 0) != (self.end_line == 0):
            raise ValueError("target coordinates must both be zero or both be positive")
        if self.start_line and self.end_line < self.start_line:
            raise ValueError("target end_line precedes start_line")
        return self


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^EV:[0-9a-f]{12}:[A-Z_]+:[A-Za-z0-9_-]+$")
    kind: EvidenceKind
    owner_id: str = Field(..., min_length=1)
    file: str = ""
    start_line: int = Field(0, ge=0)
    end_line: int = Field(0, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _closed_fact(self) -> "EvidenceFact":
        if (self.start_line == 0) != (self.end_line == 0):
            raise ValueError("evidence coordinates must both be zero or both be positive")
        if self.start_line and self.end_line < self.start_line:
            raise ValueError("evidence end_line precedes start_line")
        unknown = set(self.attributes) - _ALLOWED_EVIDENCE_ATTRIBUTES[self.kind]
        if unknown:
            raise ValueError(f"unsupported {self.kind} evidence attributes: {sorted(unknown)}")
        if self.kind == "CALL" and self.attributes.get("dependency_status") not in {
            None, "linked", "unavailable_cycle",
        }:
            raise ValueError("unsupported CALL dependency_status")
        if self.kind == "CALL" and self.attributes.get("occurrence_id") is not None:
            occurrence_id = self.attributes.get("occurrence_id")
            source_range = self.attributes.get("source_range")
            structural_role = self.attributes.get("structural_role")
            presence_status = self.attributes.get("presence_status")
            condition_fact_id = self.attributes.get("condition_fact_id")
            if not isinstance(occurrence_id, str) or not occurrence_id:
                raise ValueError("CALL occurrence_id must be nonblank")
            if (
                not isinstance(source_range, (list, tuple))
                or len(source_range) != 2
                or not all(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in source_range
                )
                or source_range[0] < 0
                or source_range[1] <= 0
            ):
                raise ValueError("CALL occurrence source_range must be exact")
            if structural_role not in {"condition", "effect"}:
                raise ValueError("unsupported CALL occurrence structural_role")
            if presence_status not in {
                "active", "inactive", "conditional", "unknown",
            }:
                raise ValueError("unsupported CALL occurrence presence_status")
            if (structural_role == "condition") != bool(condition_fact_id):
                raise ValueError("CALL occurrence condition ownership is incomplete")
        return self


class StructuralProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["structural-profile/v1"] = Field(
        "structural-profile/v1", alias="schema", serialization_alias="schema",
    )
    target: StructuralTarget
    evidence: dict[str, EvidenceFact]
    child_refs: list[str] = Field(default_factory=list)
    ordered_flow: list[str] = Field(default_factory=list)
    callee_refs: list[str] = Field(default_factory=list)
    data_object_refs: list[str] = Field(default_factory=list)
    symbol_refs: list[str] = Field(default_factory=list)
    expression_refs: list[str] = Field(default_factory=list)
    unsupported: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references(self) -> "StructuralProfile":
        if not self.evidence:
            raise ValueError("structural profile requires evidence")
        if set(self.evidence) != {fact.id for fact in self.evidence.values()}:
            raise ValueError("evidence map keys must equal EvidenceFact.id")
        expected_prefix = f"EV:{target_digest(self.target.id)}:"
        if any(not key.startswith(expected_prefix) for key in self.evidence):
            raise ValueError("evidence id belongs to a different target")
        for name in (
            "child_refs", "ordered_flow", "callee_refs", "data_object_refs", "symbol_refs",
            "expression_refs",
        ):
            refs = list(getattr(self, name))
            if len(refs) != len(set(refs)):
                raise ValueError(f"duplicate {name}")
            missing = set(refs) - set(self.evidence)
            if missing:
                raise ValueError(f"{name} contains unknown evidence: {sorted(missing)}")
        return self


class SlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(..., pattern=r"^SL:[0-9a-f]{12}:[A-Z_]+:[A-Z_]+:[A-Za-z0-9_-]+$")
    family: SlotFamily
    role: str = Field(..., min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    target_ref: str
    evidence_refs: tuple[str, ...] = Field(..., min_length=1)


class SemanticSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str
    family: SlotFamily
    role: str
    target_ref: str
    evidence_refs: tuple[str, ...]
    meaning: str
    status: EvidenceStatus
    missing_context: tuple[str, ...] = ()


class SemanticFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["semantic-frame/v1"] = Field(
        "semantic-frame/v1", alias="schema", serialization_alias="schema",
    )
    profile_schema: Literal["structural-profile/v1"] = "structural-profile/v1"
    target: StructuralTarget
    profile: StructuralProfile
    slots: dict[str, SemanticSlot]

    @model_validator(mode="after")
    def _frame_integrity(self) -> "SemanticFrame":
        if self.target != self.profile.target:
            raise ValueError("frame target and profile target differ")
        if set(self.slots) != {slot.slot_id for slot in self.slots.values()}:
            raise ValueError("slot map keys must equal SemanticSlot.slot_id")
        evidence_ids = set(self.profile.evidence)
        for slot in self.slots.values():
            if slot.target_ref not in evidence_ids:
                raise ValueError(f"unknown slot target_ref: {slot.target_ref}")
            missing = set(slot.evidence_refs) - evidence_ids
            if missing:
                raise ValueError(f"unknown slot evidence refs: {sorted(missing)}")
        return self

    def canonical_json(self) -> str:
        return _canonical(self.model_dump(mode="json", by_alias=True))


_PROMPT_FACT_FIELDS: dict[str, tuple[str, ...]] = {
    "TARGET": ("signature",),
    "CONTROL": (
        "type", "role", "condition", "condition_timing", "implicit_false",
    ),
    "STATEMENT": ("type", "effects"),
    "DATA_OBJECT": ("table_id", "access", "operation"),
    "SYMBOL": (
        "name", "scope", "data_type", "declared_value", "value_semantics",
        "initialization_sources",
    ),
    "EXTERNAL": ("semantic_kind", "matched_key"),
    "RULE": (
        "type", "role", "condition", "condition_timing",
        "implicit_false", "nested_control", "at_label",
    ),
    "TABLE": (
        "table_id", "db", "schema", "name", "source_description",
        "observed_operations", "usage_meanings",
    ),
    "COLUMN": (
        "table_id", "name", "source_description", "usage_evidence",
    ),
    "MODULE": ("module_id", "type", "member_refs", "comment_ref"),
}


def _prompt_fact_attributes(
    fact: EvidenceFact,
) -> dict[str, Any]:
    """Keep only attributes that can change the model-owned meaning leaf."""
    source = fact.attributes
    if fact.kind == "CALL":
        selected: dict[str, Any] = {
            "callee": source.get("callee_source") or source.get("callee_name"),
            "role": source.get("structural_role"),
            "dependency_status": source.get("dependency_status"),
            "recursive": source.get("recursive"),
        }
    else:
        selected = {
            name: source.get(name)
            for name in _PROMPT_FACT_FIELDS.get(fact.kind, ())
        }
        if fact.kind == "COLUMN" and selected.get("usage_evidence"):
            selected["direct_usage_sources"] = selected.pop("usage_evidence")

    if fact.kind == "DATA_OBJECT" and selected.get("table_id"):
        selected["table"] = str(selected.pop("table_id")).removeprefix("db:")

    return {
        name: value
        for name, value in selected.items()
        if value is not None and value != "" and value != [] and value != {}
    }


def profile_prompt_json(
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec] | None = None,
) -> str:
    """Render the evidence closure for one concern, not the whole target graph.

    The canonical frame retains the complete deterministic profile.  An individual model
    call only needs facts reachable from the slots assigned to that concern; repeating every
    child, rule, call and symbol in every concern made large real routines exceed provider
    context limits before inference began.
    """
    if slot_specs is None:
        return _canonical(profile.model_dump(mode="json", by_alias=True))

    specs = tuple(slot_specs)
    if not specs:
        target_ref = evidence_id(profile.target.id, "TARGET", 0)
        if target_ref not in profile.evidence:
            raise ValueError(f"prompt target evidence is missing: {target_ref}")
        return _canonical({
            "target": {
                "kind": profile.target.type,
                **_prompt_fact_attributes(profile.evidence[target_ref]),
            },
            "facts": [],
        })
    target_refs = {spec.target_ref for spec in specs}
    if len(target_refs) != 1:
        raise ValueError("one prompt profile cannot mix targets")
    target_ref = next(iter(target_refs))
    if target_ref not in profile.evidence:
        raise ValueError(f"prompt assignment references unknown target: {target_ref}")
    assigned_refs = set(
        ref for spec in specs for ref in spec.evidence_refs
    )
    missing = assigned_refs - set(profile.evidence)
    if missing:
        raise ValueError(f"prompt assignment references unknown evidence: {sorted(missing)}")
    ordered_refs = [
        ref for ref in profile.ordered_flow if ref in assigned_refs
    ]
    ordered_refs.extend(sorted(
        assigned_refs - set(ordered_refs) - target_refs,
        key=lambda ref: (
            profile.evidence[ref].start_line or 2**31,
            profile.evidence[ref].end_line or 2**31,
            profile.evidence[ref].kind,
            ref,
        ),
    ))
    fact_payload: list[dict[str, Any]] = []
    for ref in ordered_refs:
        fact = profile.evidence[ref]
        if fact.kind in {"EXPRESSION", "EXTERNAL", "TARGET"}:
            continue
        record: dict[str, Any] = {"kind": fact.kind}
        if fact.start_line:
            if fact.start_line == fact.end_line:
                record["line"] = fact.start_line
            else:
                record["lines"] = [fact.start_line, fact.end_line]
        attributes = _prompt_fact_attributes(fact)
        record.update(attributes)
        fact_payload.append(record)

    payload: dict[str, Any] = {
        "target": {
            "kind": profile.target.type,
            **_prompt_fact_attributes(profile.evidence[target_ref]),
        },
        "facts": fact_payload,
    }
    return _canonical(payload)


def merge_semantic_frames(
    *frames: SemanticFrame | None,
    required_slot_ids: Iterable[str] | None = None,
) -> SemanticFrame:
    present = tuple(frame for frame in frames if frame is not None)
    if not present:
        raise ValueError("at least one semantic frame is required")
    first = present[0]
    slots: dict[str, SemanticSlot] = {}
    for frame in present:
        if frame.target != first.target or frame.profile != first.profile:
            raise ValueError("cannot merge semantic frames from different structural profiles")
        for sid, slot in frame.slots.items():
            existing = slots.get(sid)
            if existing is not None and existing != slot:
                raise ValueError(f"conflicting semantic slot fragments: {sid}")
            slots[sid] = slot
    if required_slot_ids is not None:
        required = set(required_slot_ids)
        missing = required - set(slots)
        extra = set(slots) - required
        if missing or extra:
            raise ValueError(
                "semantic frame population mismatch: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
    return SemanticFrame(
        target=first.target,
        profile=first.profile,
        slots=slots,
    )


def mark_semantic_frame_insufficient(
    frame: SemanticFrame,
    missing_context: Iterable[str],
) -> SemanticFrame:
    """Preserve a closed frame after its generated meaning is rejected.

    Structural ownership, slot IDs and evidence coordinates remain authoritative.  The
    rejected prose must not survive, but dropping the whole fragment makes an otherwise
    valid target disappear from source-free consumers.  Replace only the model-owned
    leaves with an explicit insufficient state so consumers can see the gap without
    receiving fabricated or rejected meaning.
    """
    reasons = tuple(dict.fromkeys(
        " ".join(str(item).split()) for item in missing_context if str(item).strip()
    ))
    if not reasons:
        raise ValueError("insufficient semantic frame requires missing_context")
    return SemanticFrame(
        target=frame.target,
        profile=frame.profile,
        slots={
            slot_id: SemanticSlot(
                slot_id=slot.slot_id,
                family=slot.family,
                role=slot.role,
                target_ref=slot.target_ref,
                evidence_refs=slot.evidence_refs,
                meaning="",
                status="insufficient",
                missing_context=reasons,
            )
            for slot_id, slot in frame.slots.items()
        },
    )
