"""Canonical Framework call-occurrence resolution evidence.

Parser owns syntax/ranges and Analyzer owns routine binding plus deterministic
resolution.  This model joins those facts without reading source again and is shared by
workflow, Neo4j generation closure and API/MCP readers.
"""
from __future__ import annotations

import json
import hashlib
from collections import Counter
from typing import Any, Literal, Mapping, Sequence

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


CallOutcome = Literal["edge", "excluded", "preserved", "unresolved"]


class CallEvidenceRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    char_offset: int = Field(
        ..., ge=0, validation_alias=AliasChoices("char_offset", "charOffset"),
    )
    char_length: int = Field(
        ..., ge=0, validation_alias=AliasChoices("char_length", "charLength"),
    )

    @property
    def end(self) -> int:
        return self.char_offset + self.char_length


class CallResolutionOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(..., min_length=1)
    ordinal: int = Field(..., ge=0)
    source_id: str = Field(..., min_length=1)
    caller_id: str = ""
    owner_status: Literal["resolved", "unresolved_no_routine"]
    file: str = Field(..., min_length=1)
    line: int = Field(..., ge=1)
    range: CallEvidenceRange
    callee_range: CallEvidenceRange
    argument_ranges: tuple[CallEvidenceRange, ...] = ()
    callee_kind: Literal["named", "constructor", "expression"]
    terminal_name: str = ""
    grammar_rule: str = Field(..., min_length=1)
    presence_status: Literal["active", "inactive", "conditional", "unknown"]
    presence: dict[str, Any]
    configured_preprocessing_required: bool = False
    outcome: CallOutcome
    reason: str = ""
    scope: Literal["internal", "external"] | None = None
    callee_id: str = ""
    preprocessing_evidence_ids: tuple[str, ...] = ()
    macro_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _closed_occurrence(self) -> "CallResolutionOccurrence":
        if self.range.char_length == 0:
            raise ValueError("call range must be nonempty")
        if (
            self.callee_range.char_length == 0
            or self.callee_range.char_offset < self.range.char_offset
            or self.callee_range.end > self.range.end
        ):
            raise ValueError("callee range must be a nonempty call subrange")
        previous_end = self.callee_range.end
        for argument in self.argument_ranges:
            if (
                argument.char_offset < self.range.char_offset
                or argument.end > self.range.end
                or argument.char_offset < previous_end
            ):
                raise ValueError("argument ranges must be ordered call subranges")
            previous_end = argument.end
        if self.callee_kind == "expression":
            if self.terminal_name:
                raise ValueError("expression callee cannot have terminal_name")
        elif not self.terminal_name:
            raise ValueError("named/constructor call requires terminal_name")
        if self.presence.get("status") != self.presence_status:
            raise ValueError("presence table value differs from presence_status")
        if bool(self.caller_id) != (self.owner_status == "resolved"):
            raise ValueError("caller_id and owner_status differ")
        if not self.caller_id and not (
            self.outcome == "unresolved"
            and self.reason == "insufficient_framework_routine_owner"
        ):
            raise ValueError("ownerless call must remain explicitly unresolved")
        if self.outcome == "edge":
            if self.reason or self.scope is None or not self.callee_id:
                raise ValueError("edge outcome requires scope/callee_id and no reason")
        elif not self.reason or self.scope is not None or self.callee_id:
            raise ValueError(
                "non-edge outcome requires reason and cannot claim scope/callee_id"
            )
        for name, values in (
            ("preprocessing_evidence_ids", self.preprocessing_evidence_ids),
            ("macro_fact_ids", self.macro_fact_ids),
        ):
            if any(not value for value in values) or len(values) != len(set(values)):
                raise ValueError(f"{name} must contain unique nonblank IDs")
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def call_occurrence_node_id(call_id: str) -> str:
    value = str(call_id or "")
    if not value:
        raise ValueError("call occurrence node requires canonical call_id")
    return f"call-occurrence:{value}"


def build_call_resolution_ledger(
    call_sites: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join Parser/owner occurrence fields to one classifier outcome per ID."""
    if len(call_sites) != len(outcomes):
        raise ValueError(
            f"call occurrence result population mismatch: "
            f"sites={len(call_sites)}, outcomes={len(outcomes)}"
        )
    rows: list[dict[str, Any]] = []
    for ordinal, (site, outcome) in enumerate(zip(call_sites, outcomes, strict=True)):
        call_id = str(site.get("call_id") or "")
        if call_id != str(outcome.get("call_id") or ""):
            raise ValueError(f"call occurrence result order mismatch at ordinal {ordinal}")
        row = {
            "call_id": call_id,
            "ordinal": ordinal,
            "source_id": str(site.get("source_id") or ""),
            "caller_id": str(site.get("caller_id") or ""),
            "owner_status": str(site.get("owner_status") or ""),
            "file": str(site.get("file") or ""),
            "line": site.get("line"),
            "range": site.get("range"),
            "callee_range": site.get("callee_range"),
            "argument_ranges": list(site.get("argument_ranges") or []),
            "callee_kind": site.get("callee_kind"),
            "terminal_name": str(site.get("call_text") or ""),
            "grammar_rule": str(site.get("grammar_rule") or ""),
            "presence_status": site.get("presence_status"),
            "presence": dict(site.get("presence") or {}),
            "configured_preprocessing_required": (
                site.get("configured_preprocessing_required") is True
            ),
            "outcome": outcome.get("outcome"),
            "reason": str(outcome.get("reason") or ""),
            "scope": outcome.get("scope"),
            "callee_id": str(outcome.get("callee_id") or ""),
            "preprocessing_evidence_ids": sorted(set(
                str(value) for value in outcome.get("preprocessing_evidence_ids") or []
            )),
            "macro_fact_ids": sorted(set(
                str(value) for value in outcome.get("macro_fact_ids") or []
            )),
        }
        rows.append(CallResolutionOccurrence.model_validate(row).as_dict())
    return normalize_call_resolution_ledger(rows)


def normalize_call_resolution_ledger(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = [CallResolutionOccurrence.model_validate(row).as_dict() for row in rows]
    ids = [row["call_id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("call occurrence ledger contains duplicate canonical call_id")
    ordinals = [row["ordinal"] for row in normalized]
    if ordinals != list(range(len(normalized))):
        raise ValueError("call occurrence ledger ordinals must preserve complete source order")
    return normalized


def normalize_call_resolution_subset(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = [CallResolutionOccurrence.model_validate(row).as_dict() for row in rows]
    normalized.sort(key=lambda row: row["ordinal"])
    ids = [row["call_id"] for row in normalized]
    ordinals = [row["ordinal"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("call occurrence subset contains duplicate canonical call_id")
    if len(ordinals) != len(set(ordinals)):
        raise ValueError("call occurrence subset contains duplicate source ordinal")
    return normalized


def summarize_call_resolution_ledger(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_call_resolution_ledger(rows)
    outcomes = Counter(row["outcome"] for row in normalized)
    reasons = Counter(row["reason"] for row in normalized if row["reason"])
    return {
        "total": len(normalized),
        "unresolved": outcomes.get("unresolved", 0),
        "by_outcome": dict(sorted(outcomes.items())),
        "by_reason": dict(sorted(reasons.items())),
        "sha256": hashlib.sha256(
            canonical_json(normalized).encode("utf-8")
        ).hexdigest(),
    }


def summarize_call_resolution_subset(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_call_resolution_subset(rows)
    outcomes = Counter(row["outcome"] for row in normalized)
    reasons = Counter(row["reason"] for row in normalized if row["reason"])
    return {
        "total": len(normalized),
        "unresolved": outcomes.get("unresolved", 0),
        "by_outcome": dict(sorted(outcomes.items())),
        "by_reason": dict(sorted(reasons.items())),
        "sha256": hashlib.sha256(
            canonical_json(normalized).encode("utf-8")
        ).hexdigest(),
    }


def validate_call_resolution_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("call resolution summary object required")
    required = {"total", "unresolved", "by_outcome", "by_reason", "sha256"}
    if set(value) != required:
        raise ValueError("call resolution summary fields are incomplete or unknown")
    total = value["total"]
    unresolved = value["unresolved"]
    by_outcome = value["by_outcome"]
    by_reason = value["by_reason"]
    digest = value["sha256"]
    if (
        isinstance(total, bool) or not isinstance(total, int) or total < 0
        or isinstance(unresolved, bool) or not isinstance(unresolved, int)
        or unresolved < 0
    ):
        raise ValueError("call resolution summary counts must be nonnegative integers")
    if not isinstance(by_outcome, dict) or not isinstance(by_reason, dict):
        raise ValueError("call resolution summary breakdown objects required")
    allowed = {"edge", "excluded", "preserved", "unresolved"}
    if set(by_outcome) - allowed or any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in (*by_outcome.values(), *by_reason.values())
    ):
        raise ValueError("call resolution summary breakdown is invalid")
    if sum(by_outcome.values()) != total or by_outcome.get("unresolved", 0) != unresolved:
        raise ValueError("call resolution summary accounting does not close")
    if not isinstance(digest, str) or len(digest) != 64 or any(
        char not in "0123456789abcdef" for char in digest
    ):
        raise ValueError("call resolution summary sha256 is invalid")
    return {
        "total": total,
        "unresolved": unresolved,
        "by_outcome": dict(sorted(by_outcome.items())),
        "by_reason": dict(sorted(by_reason.items())),
        "sha256": digest,
    }


def call_resolution_storage_item(row: Mapping[str, Any]) -> dict[str, Any]:
    occurrence = CallResolutionOccurrence.model_validate(row)
    value = occurrence.as_dict()
    return {
        "id": call_occurrence_node_id(occurrence.call_id),
        "call_id": occurrence.call_id,
        "ordinal": occurrence.ordinal,
        "source_id": occurrence.source_id,
        "caller_id": occurrence.caller_id,
        "owner_status": occurrence.owner_status,
        "file": occurrence.file,
        "line": occurrence.line,
        "range_char_offset": occurrence.range.char_offset,
        "range_char_length": occurrence.range.char_length,
        "callee_range_char_offset": occurrence.callee_range.char_offset,
        "callee_range_char_length": occurrence.callee_range.char_length,
        "argument_ranges_json": canonical_json(value["argument_ranges"]),
        "callee_kind": occurrence.callee_kind,
        "terminal_name": occurrence.terminal_name,
        "grammar_rule": occurrence.grammar_rule,
        "presence_status": occurrence.presence_status,
        "presence_json": canonical_json(occurrence.presence),
        "configured_preprocessing_required": occurrence.configured_preprocessing_required,
        "outcome": occurrence.outcome,
        "reason": occurrence.reason,
        "scope": occurrence.scope or "",
        "callee_id": occurrence.callee_id,
        "preprocessing_evidence_ids": list(occurrence.preprocessing_evidence_ids),
        "macro_fact_ids": list(occurrence.macro_fact_ids),
    }


def call_resolution_from_storage(properties: Mapping[str, Any]) -> dict[str, Any]:
    try:
        arguments = json.loads(str(properties.get("argument_ranges_json") or "[]"))
        presence = json.loads(str(properties.get("presence_json") or "{}"))
    except json.JSONDecodeError as error:
        raise ValueError("invalid persisted call occurrence JSON") from error
    row = {
        "call_id": properties.get("call_id"),
        "ordinal": properties.get("ordinal"),
        "source_id": properties.get("source_id"),
        "caller_id": properties.get("caller_id") or "",
        "owner_status": properties.get("owner_status"),
        "file": properties.get("file"),
        "line": properties.get("line"),
        "range": {
            "char_offset": properties.get("range_char_offset"),
            "char_length": properties.get("range_char_length"),
        },
        "callee_range": {
            "char_offset": properties.get("callee_range_char_offset"),
            "char_length": properties.get("callee_range_char_length"),
        },
        "argument_ranges": arguments,
        "callee_kind": properties.get("callee_kind"),
        "terminal_name": properties.get("terminal_name") or "",
        "grammar_rule": properties.get("grammar_rule"),
        "presence_status": properties.get("presence_status"),
        "presence": presence,
        "configured_preprocessing_required": (
            properties.get("configured_preprocessing_required") is True
        ),
        "outcome": properties.get("outcome"),
        "reason": properties.get("reason") or "",
        "scope": properties.get("scope") or None,
        "callee_id": properties.get("callee_id") or "",
        "preprocessing_evidence_ids": list(
            properties.get("preprocessing_evidence_ids") or []
        ),
        "macro_fact_ids": list(properties.get("macro_fact_ids") or []),
    }
    return CallResolutionOccurrence.model_validate(row).as_dict()
