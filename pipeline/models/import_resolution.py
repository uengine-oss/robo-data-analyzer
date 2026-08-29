"""Canonical Parser import-entry resolution evidence.

Parser owns statement and binding syntax. Analyzer may publish a cross-module scope only
when a separate environment-aware binder proves the target. Merely finding a same-named
source file is never resolution evidence.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Literal, Mapping, Sequence

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


ImportOutcome = Literal["resolved", "excluded", "unresolved"]
ImportEvidenceVersion = Literal["1.0.0", "1.1.0", "1.2.0", "2.0.0"]
_STRUCTURED_IMPORT_EVIDENCE_VERSIONS = frozenset({"1.1.0", "1.2.0", "2.0.0"})


class ImportEvidenceRange(BaseModel):
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


def import_occurrence_id(import_id: str, entry_ordinal: int) -> str:
    fact_id = str(import_id or "")
    if (
        len(fact_id) != 64
        or any(character not in "0123456789abcdef" for character in fact_id)
        or entry_ordinal < 0
    ):
        raise ValueError("import occurrence requires canonical fact ID and entry ordinal")
    return f"{fact_id}:{entry_ordinal}"


class ImportResolutionOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_id: str = Field(..., min_length=1)
    import_id: str = Field(..., min_length=64, max_length=64)
    ordinal: int = Field(..., ge=0)
    entry_ordinal: int = Field(..., ge=0)
    evidence_version: ImportEvidenceVersion
    source_id: str = Field(..., min_length=1)
    line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    range: ImportEvidenceRange
    directive_kind: Literal["include", "import"]
    grammar_rule: str = Field(..., min_length=1)
    presence_status: Literal["active", "inactive", "conditional", "unknown"]
    evidence_status: Literal["complete", "partial"]
    import_kind: Literal[
        "source_file", "computed", "type", "namespace", "static_member",
        "module", "module_member",
    ]
    target_kind: Literal["quoted", "angle", "computed", "qualified"]
    target: str = Field(..., min_length=1)
    target_range: ImportEvidenceRange
    path_components: tuple[str, ...] = ()
    path_component_ranges: tuple[ImportEvidenceRange, ...] = ()
    member: str | None = None
    member_range: ImportEvidenceRange | None = None
    alias: str | None = None
    alias_range: ImportEvidenceRange | None = None
    relative_level: int = Field(..., ge=0)
    wildcard: bool
    locality: Literal["local", "system", "unspecified"]
    outcome: ImportOutcome
    reason: str = ""
    resolved_module_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _closed_occurrence(self) -> "ImportResolutionOccurrence":
        if self.occurrence_id != import_occurrence_id(
            self.import_id, self.entry_ordinal,
        ):
            raise ValueError("import occurrence identity differs from fact/entry identity")
        if self.range.char_length == 0 or self.end_line < self.line:
            raise ValueError("import statement range/lines must be nonempty and ordered")
        if (
            self.target_range.char_length == 0
            or self.target_range.char_offset < self.range.char_offset
            or self.target_range.end > self.range.end
        ):
            raise ValueError("target range must be a nonempty import statement subrange")
        if len(self.path_components) != len(self.path_component_ranges):
            raise ValueError("path component values and ranges differ")
        previous_end = -1
        for value, component in zip(
            self.path_components, self.path_component_ranges, strict=True,
        ):
            if (
                not value or component.char_length == 0
                or component.char_offset < self.range.char_offset
                or component.end > self.range.end
                or component.char_offset < previous_end
            ):
                raise ValueError("path component ranges must be ordered statement subranges")
            previous_end = component.end
        for name, value, value_range in (
            ("member", self.member, self.member_range),
            ("alias", self.alias, self.alias_range),
        ):
            if (value is None) != (value_range is None):
                raise ValueError(f"{name} value and range must be present together")
            if value_range is not None and (
                not value or value_range.char_length == 0
                or value_range.char_offset < self.range.char_offset
                or value_range.end > self.range.end
            ):
                raise ValueError(f"{name} range must be a nonempty statement subrange")
        if self.evidence_version == "1.0.0" and self.outcome == "resolved":
            raise ValueError("legacy import evidence cannot authorize resolution")
        if self.presence_status != "active" and self.outcome == "resolved":
            raise ValueError("non-active import cannot authorize resolution")
        if self.evidence_status != "complete" and self.outcome == "resolved":
            raise ValueError("incomplete import evidence cannot authorize resolution")
        if self.outcome == "resolved":
            if self.reason or not self.resolved_module_ids:
                raise ValueError("resolved import requires targets and no degraded reason")
        elif not self.reason or self.resolved_module_ids:
            raise ValueError(
                "non-resolved import requires reason and cannot claim resolved targets"
            )
        if (
            any(not value for value in self.resolved_module_ids)
            or len(self.resolved_module_ids) != len(set(self.resolved_module_ids))
        ):
            raise ValueError("resolved module IDs must be unique and nonblank")
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _outcome(ref: Mapping[str, Any], entry: Mapping[str, Any]) -> tuple[str, str]:
    version = str(ref.get("evidence_version") or "")
    status = str(ref.get("evidence_status") or "")
    presence = str(ref.get("presence_status") or "")
    if version not in _STRUCTURED_IMPORT_EVIDENCE_VERSIONS:
        return "unresolved", "insufficient_structured_import_evidence"
    if status != "complete":
        return "unresolved", "insufficient_import_evidence_completeness"
    if presence == "inactive":
        return "excluded", "inactive_import"
    if presence != "active":
        return "unresolved", "insufficient_import_presence"
    if entry.get("import_kind") == "computed":
        return "unresolved", "insufficient_computed_import_target"
    return "unresolved", "insufficient_import_binding_environment"


def build_import_resolution_ledger(
    import_refs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Produce exactly one explicit outcome for every canonical import binding entry."""
    rows: list[dict[str, Any]] = []
    seen_fact_ids: set[str] = set()
    for ref in import_refs:
        import_id = str(ref.get("import_id") or "")
        if not import_id:
            # Parser explicitly unsupported/not-applicable projections have no fact.
            continue
        if import_id in seen_fact_ids:
            raise ValueError(f"duplicate canonical import fact: {import_id}")
        seen_fact_ids.add(import_id)
        entries = ref.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"canonical import fact has no binding entries: {import_id}")
        for expected_entry_ordinal, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise ValueError(f"import binding entry must be an object: {import_id}")
            entry_ordinal = entry.get("entry_ordinal")
            if entry_ordinal != expected_entry_ordinal:
                raise ValueError(
                    f"import binding entry order mismatch: {import_id}:"
                    f"{entry_ordinal}!={expected_entry_ordinal}"
                )
            outcome, reason = _outcome(ref, entry)
            occurrence_id = import_occurrence_id(import_id, expected_entry_ordinal)
            row = {
                "occurrence_id": occurrence_id,
                "import_id": import_id,
                "ordinal": len(rows),
                "entry_ordinal": expected_entry_ordinal,
                "evidence_version": ref.get("evidence_version"),
                "source_id": ref.get("source_id"),
                "line": ref.get("line"),
                "end_line": ref.get("end_line"),
                "range": ref.get("range"),
                "directive_kind": ref.get("directive_kind"),
                "grammar_rule": ref.get("grammar_rule"),
                "presence_status": ref.get("presence_status"),
                "evidence_status": ref.get("evidence_status"),
                "import_kind": entry.get("import_kind"),
                "target_kind": entry.get("target_kind"),
                "target": entry.get("target"),
                "target_range": entry.get("target_range"),
                "path_components": list(entry.get("path_components") or []),
                "path_component_ranges": list(
                    entry.get("path_component_ranges") or []
                ),
                "member": entry.get("member"),
                "member_range": entry.get("member_range"),
                "alias": entry.get("alias"),
                "alias_range": entry.get("alias_range"),
                "relative_level": entry.get("relative_level"),
                "wildcard": entry.get("wildcard"),
                "locality": entry.get("locality"),
                "outcome": outcome,
                "reason": reason,
                "resolved_module_ids": [],
            }
            rows.append(ImportResolutionOccurrence.model_validate(row).as_dict())
    return normalize_import_resolution_ledger(rows)


def normalize_import_resolution_ledger(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = [
        ImportResolutionOccurrence.model_validate(row).as_dict() for row in rows
    ]
    occurrence_ids = [row["occurrence_id"] for row in normalized]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise ValueError("import occurrence ledger contains duplicate identities")
    if [row["ordinal"] for row in normalized] != list(range(len(normalized))):
        raise ValueError("import occurrence ledger ordinals must preserve source order")
    by_fact: dict[str, list[int]] = {}
    for row in normalized:
        by_fact.setdefault(row["import_id"], []).append(row["entry_ordinal"])
    if any(values != list(range(len(values))) for values in by_fact.values()):
        raise ValueError("import entry ordinals must be exact-once per fact")
    return normalized


def summarize_import_resolution_ledger(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_import_resolution_ledger(rows)
    outcomes = Counter(row["outcome"] for row in normalized)
    reasons = Counter(row["reason"] for row in normalized if row["reason"])
    canonical = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return {
        "total": len(normalized),
        "unresolved": outcomes.get("unresolved", 0),
        "by_outcome": dict(sorted(outcomes.items())),
        "by_reason": dict(sorted(reasons.items())),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def normalize_import_resolution_subset(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized = [
        ImportResolutionOccurrence.model_validate(row).as_dict() for row in rows
    ]
    normalized.sort(key=lambda row: row["ordinal"])
    occurrence_ids = [row["occurrence_id"] for row in normalized]
    ordinals = [row["ordinal"] for row in normalized]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise ValueError("import occurrence subset contains duplicate identities")
    if len(ordinals) != len(set(ordinals)):
        raise ValueError("import occurrence subset contains duplicate source ordinals")
    return normalized


def summarize_import_resolution_subset(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_import_resolution_subset(rows)
    outcomes = Counter(row["outcome"] for row in normalized)
    reasons = Counter(row["reason"] for row in normalized if row["reason"])
    canonical = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return {
        "total": len(normalized),
        "unresolved": outcomes.get("unresolved", 0),
        "by_outcome": dict(sorted(outcomes.items())),
        "by_reason": dict(sorted(reasons.items())),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def validate_import_resolution_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("import resolution summary object required")
    required = {"total", "unresolved", "by_outcome", "by_reason", "sha256"}
    if set(value) != required:
        raise ValueError("import resolution summary fields are incomplete or unknown")
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
        raise ValueError("import resolution summary counts must be nonnegative integers")
    if not isinstance(by_outcome, dict) or not isinstance(by_reason, dict):
        raise ValueError("import resolution summary breakdown objects required")
    if set(by_outcome) - {"resolved", "excluded", "unresolved"} or any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in (*by_outcome.values(), *by_reason.values())
    ):
        raise ValueError("import resolution summary breakdown is invalid")
    if sum(by_outcome.values()) != total or by_outcome.get("unresolved", 0) != unresolved:
        raise ValueError("import resolution summary accounting does not close")
    if (
        not isinstance(digest, str) or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("import resolution summary sha256 is invalid")
    return {
        "total": total,
        "unresolved": unresolved,
        "by_outcome": dict(sorted(by_outcome.items())),
        "by_reason": dict(sorted(by_reason.items())),
        "sha256": digest,
    }
