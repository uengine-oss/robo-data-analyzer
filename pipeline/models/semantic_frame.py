"""Canonical bounded semantic-frame contracts.

Deterministic code owns target, evidence, slot metadata and evidence sufficiency.
The model owns only one meaning string for every preassigned slot.  Boundedness comes
from deterministic evidence-closure partitioning, never from truncating a canonical
meaning or diagnostic leaf.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator


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


# This order is part of the legacy summary and aggregate-member projection contract.
# Keeping it beside the canonical frame types prevents planner, projection, batching,
# and aggregate consumers from drifting into different definitions of completeness.
FUNCTION_SUMMARY_ROLES = (
    "RESPONSIBILITY",
    "INPUT_ROLE",
    "GUARDS_AND_BRANCHES",
    "PROCESSING_EFFECTS",
    "RESULT",
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


def semantic_slot_wire_ids(slot_specs: Iterable[SlotSpec]) -> tuple[str, ...]:
    """Return request-local compact keys for an exact ordered slot assignment.

    Canonical ``SL:...`` identifiers belong to the Analyzer frame, graph and MCP
    boundaries.  Echoing them through every constrained model response wastes decode
    tokens and made the dynamic frame materially slower than the legacy pipeline.  The
    provider only needs an unambiguous key inside one request.  A plain ``S0`` namespace
    collides with real legacy literals and was copied into APAS Rule meanings.  Include
    the exact assignment digest in every private key so ordinary source values cannot be
    confused with provider coordinates, then restore canonical identifiers by the model's
    closed binding.  Missing, duplicate and extra values remain rejected; no response
    order is trusted.
    """
    specs = tuple(slot_specs)
    if not specs or len({spec.slot_id for spec in specs}) != len(specs):
        raise ValueError("semantic frame slot specs must be non-empty and unique")
    namespace = stable_digest([spec.slot_id for spec in specs], length=10)
    return tuple(f"W{namespace}_{index}" for index in range(len(specs)))


def semantic_fact_wire_bindings(
    slot_specs: Iterable[SlotSpec],
) -> dict[str, str]:
    """Map irreducible assigned facts to request-local model-input references.

    Canonical evidence IDs remain in the Analyzer ledger and never become model field
    names.  The model receives only compact request-local fact references so a slot can
    point at one non-duplicated structural fact without receiving target digests or
    producer identities. Canonical EXPRESSION facts stay in the Analyzer ledger instead
    of being sent beside the exact source and the already-normalized control, statement,
    and Rule facts. That duplicate tree does not add a model-owned decision.
    """
    specs = tuple(slot_specs)
    if not specs:
        raise ValueError("semantic evidence bindings require at least one slot")
    target_refs = {spec.target_ref for spec in specs}
    if len(target_refs) != 1:
        raise ValueError("one prompt assignment cannot mix target evidence")
    refs = tuple(dict.fromkeys(
        ref
        for spec in specs
        for ref in spec.evidence_refs
        if ref not in target_refs and ":EXPRESSION:" not in ref
    ))
    namespace = stable_digest(refs, length=8)
    return {ref: f"E{namespace}_{index}" for index, ref in enumerate(refs)}


def _mentions_private_wire_id(text: str, wire_ids: Iterable[str]) -> bool:
    value = str(text or "")
    return any(re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(wire_id)}(?![A-Za-z0-9_])",
        value,
    ) for wire_id in wire_ids)


def semantic_frame_response_model(slot_specs: Iterable[SlotSpec]) -> type[BaseModel]:
    specs = tuple(slot_specs)
    wire_ids = semantic_slot_wire_ids(specs)
    fingerprint = stable_digest([spec.model_dump(mode="json") for spec in specs])
    fields = {wire_id: (str, Field(...)) for wire_id in wire_ids}
    slots_model = create_model(
        f"AssignedSemanticSlotMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    model = create_model(
        f"AssignedSemanticFrameOutput_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        slots=(slots_model, Field(...)),
    )
    # Runtime-only diagnostics/test metadata.  This is deliberately absent from the
    # provider schema and therefore cannot increase prompt or decode size.
    model.__semantic_slot_bindings__ = tuple(zip(wire_ids, specs, strict=True))
    return model


def normalize_semantic_frame_response(
    output: BaseModel,
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
) -> SemanticFrame:
    specs = tuple(slot_specs)
    wire_ids = semantic_slot_wire_ids(specs)
    evidence_ids = set(profile.evidence)
    target_ref = evidence_id(profile.target.id, "TARGET", 0)
    if target_ref not in evidence_ids:
        raise ValueError("profile target evidence is missing")
    slots: dict[str, SemanticSlot] = {}
    private_wire_ids = (
        *wire_ids,
        *semantic_fact_wire_bindings(specs).values(),
    )
    for spec, wire_id in zip(specs, wire_ids, strict=True):
        if spec.target_ref != target_ref:
            raise ValueError(f"slot target_ref differs from canonical target: {spec.slot_id}")
        missing = set(spec.evidence_refs) - evidence_ids
        if missing:
            raise ValueError(f"slot spec references unknown evidence: {sorted(missing)}")
        leaf = getattr(output.slots, wire_id)
        leaf_texts = (leaf,)
        unavailable_callees = sorted({
            str(fact.attributes.get("callee_id") or "")
            for ref in spec.evidence_refs
            if (fact := profile.evidence[ref]).kind == "CALL"
            and fact.attributes.get("dependency_status") == "unavailable_cycle"
            and str(fact.attributes.get("callee_id") or "")
        })
        leaked_private_key = any(
            _mentions_private_wire_id(text, private_wire_ids) for text in leaf_texts
        )
        if leaked_private_key:
            # The raw provider artifact still owns the exact bad output.  Canonical
            # meaning must not carry request-local coordinates, and pretending that a
            # string replacement repaired the meaning would create a quiet success.
            meaning = ""
            status = "insufficient"
            missing_context = (
                "model output referenced a request-local private response key",
            )
        else:
            meaning = leaf.strip()
            status = "sufficient" if meaning else "insufficient"
            missing_context = () if meaning else (
                f"assigned {spec.family}.{spec.role} returned no meaning",
            )
        if unavailable_callees:
            dependency_reasons = tuple(
                f"callee semantic dependency unavailable in call cycle: {callee_id}"
                for callee_id in unavailable_callees
            )
            missing_context = tuple(dict.fromkeys((
                *missing_context, *dependency_reasons,
            )))
            status = "partial" if meaning else "insufficient"
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
    return SemanticFrame(
        target=profile.target,
        profile=profile,
        slots=slots,
    )


def slot_specs_json(slot_specs: Iterable[SlotSpec]) -> str:
    specs = tuple(slot_specs)
    wire_ids = semantic_slot_wire_ids(specs)
    fact_bindings = semantic_fact_wire_bindings(specs)
    return _canonical({
        wire_id: {
            "kind": f"{spec.family}.{spec.role}",
            "fact_refs": tuple(
                fact_bindings[ref]
                for ref in spec.evidence_refs
                if ref in fact_bindings
            ),
        }
        for spec, wire_id in zip(specs, wire_ids, strict=True)
    })


_PROMPT_FACT_FIELDS: dict[str, tuple[str, ...]] = {
    "TARGET": ("signature",),
    "CONTROL": (
        "type", "role", "condition", "condition_timing", "parent_ref",
        "complement_of", "implicit_false",
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
        "complement_of", "implicit_false", "nested_control", "fallthrough_to",
        "at_label", "after_control",
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
    fact_bindings: Mapping[str, str],
    node_bindings: Mapping[str, str],
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

    def localize(value: Any) -> Any:
        if isinstance(value, str):
            return fact_bindings.get(value, node_bindings.get(value, value))
        if isinstance(value, list):
            return [localize(item) for item in value]
        if isinstance(value, tuple):
            return [localize(item) for item in value]
        if isinstance(value, dict):
            return {key: localize(item) for key, item in value.items()}
        return value

    return {
        name: localize(value)
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
    fact_bindings = semantic_fact_wire_bindings(specs)
    target_refs = {spec.target_ref for spec in specs}
    if len(target_refs) != 1:
        raise ValueError("one prompt profile cannot mix targets")
    target_ref = next(iter(target_refs))
    if target_ref not in profile.evidence:
        raise ValueError(f"prompt assignment references unknown target: {target_ref}")
    missing = set(fact_bindings) - set(profile.evidence)
    if missing:
        raise ValueError(f"prompt assignment references unknown evidence: {sorted(missing)}")
    facts = {
        ref: profile.evidence[ref] for ref in fact_bindings
    }
    node_bindings = {
        str(fact.attributes["node_id"]): fact_bindings[ref]
        for ref, fact in facts.items()
        if fact.attributes.get("node_id")
    }
    fact_payload: dict[str, dict[str, Any]] = {}
    for ref, fact in facts.items():
        record: dict[str, Any] = {"kind": fact.kind}
        if fact.start_line:
            if fact.start_line == fact.end_line:
                record["line"] = fact.start_line
            else:
                record["lines"] = [fact.start_line, fact.end_line]
        attributes = _prompt_fact_attributes(
            fact, fact_bindings, node_bindings,
        )
        record.update(attributes)
        fact_payload[fact_bindings[ref]] = record

    payload: dict[str, Any] = {
        "target": {
            "kind": profile.target.type,
            **_prompt_fact_attributes(
                profile.evidence[target_ref], fact_bindings, node_bindings,
            ),
        },
        "facts": fact_payload,
        "ordered_flow": [
            fact_bindings[ref]
            for ref in profile.ordered_flow
            if ref in fact_bindings
        ],
    }
    if profile.unsupported:
        payload["unsupported"] = profile.unsupported
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
