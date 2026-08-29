"""Canonical Parser call-occurrence resolution evidence.

Parser owns syntax/ranges and Analyzer owns routine binding plus deterministic
resolution.  This model joins those facts without reading source again and is shared by
workflow, Neo4j generation closure and API/MCP readers.
"""
from __future__ import annotations

import json
import hashlib
from collections import Counter
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


CallOutcome = Literal["edge", "excluded", "preserved", "unresolved"]
ResolutionStatus = Literal[
    "internal", "external", "dynamic", "configuration_dependent",
    "ambiguous", "unsupported", "inactive", "standard_library",
    "framework_provided",
]


class CallResolutionOccurrence(BaseModel):
    """One Analyzer decision for one canonical Parser/AST call occurrence.

    Parser-owned source text, ranges, grammar and presence remain in the
    structural occurrence.  Repeating them here made the resolution ledger a
    second, drift-prone copy of the Parser artifact.  This ledger keeps only
    the exact-once identity and the Analyzer-owned binding/classification
    decision needed by product projection, diagnostics and publication.
    """

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(..., min_length=1)
    ordinal: int = Field(..., ge=0)
    caller_id: str = ""
    resolution_status: ResolutionStatus
    adapter_schema: str = Field(..., min_length=1)
    outcome: CallOutcome
    reason: str = ""
    scope: Literal["internal", "external"] | None = None
    callee_id: str = ""
    external_identity_key: str = ""
    declaration_fact_id: str = ""
    candidate_fact_ids: tuple[str, ...] = ()
    preprocessing_evidence_ids: tuple[str, ...] = ()
    macro_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _closed_occurrence(self) -> "CallResolutionOccurrence":
        if not self.caller_id and not (
            self.outcome == "unresolved"
            and self.reason in {
                "insufficient_routine_owner",
            }
        ):
            raise ValueError("ownerless call must remain explicitly unresolved")
        if self.outcome == "edge":
            if self.reason or self.scope is None or not self.callee_id:
                raise ValueError("edge outcome requires scope/callee_id and no reason")
        elif not self.reason or self.scope is not None or self.callee_id:
            raise ValueError(
                "non-edge outcome requires reason and cannot claim scope/callee_id"
            )
        if self.resolution_status == "internal":
            if self.outcome != "edge" or self.scope != "internal":
                raise ValueError("internal resolution requires one internal edge")
            if self.external_identity_key:
                raise ValueError("internal resolution cannot claim external identity")
        elif self.resolution_status == "external":
            if self.outcome != "preserved" or not self.external_identity_key:
                raise ValueError("external resolution requires preserved collision-free identity")
        elif self.resolution_status == "inactive":
            if self.outcome != "preserved" or self.reason != "inactive_presence":
                raise ValueError("inactive resolution requires preserved inactive evidence")
        elif self.resolution_status in {"standard_library", "framework_provided"}:
            if self.outcome != "excluded" or not self.reason:
                raise ValueError(
                    f"{self.resolution_status} resolution requires an excluded outcome"
                )
        elif self.outcome != "unresolved":
            raise ValueError(f"{self.resolution_status} resolution must remain unresolved")
        for name, values in (
            ("candidate_fact_ids", self.candidate_fact_ids),
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
            "caller_id": str(site.get("caller_id") or ""),
            "resolution_status": outcome.get("resolution_status"),
            "adapter_schema": str(outcome.get("adapter_schema") or ""),
            "outcome": outcome.get("outcome"),
            "reason": str(outcome.get("reason") or ""),
            "scope": outcome.get("scope"),
            "callee_id": str(outcome.get("callee_id") or ""),
            "external_identity_key": str(
                outcome.get("external_identity_key") or ""
            ),
            "declaration_fact_id": str(outcome.get("declaration_fact_id") or ""),
            "candidate_fact_ids": sorted(set(
                str(value) for value in outcome.get("candidate_fact_ids") or []
            )),
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
