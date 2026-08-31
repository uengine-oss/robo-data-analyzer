"""Canonical publication state for one analyzed function meaning.

The model response never owns these records.  Analyzer derives them from the closed
Summary semantic obligations after candidate validation.  Detailed missing context is
kept in a run artifact; the product CodeNode stores only its status, item count and the
artifact content hash.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.semantic_frame import FUNCTION_SUMMARY_ROLES, SemanticFrame
from pipeline.languages import ROUTINE_LABELS


MeaningStatus = Literal["sufficient", "partial", "insufficient"]


class MissingContextItem(BaseModel):
    """One stable Analyzer-owned explanation of an unclosed meaning obligation."""

    model_config = ConfigDict(extra="forbid")

    missing_context_ref: str = Field(..., min_length=1)
    reason_code: str = Field(..., min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(..., min_length=1)


class FunctionMeaningState(BaseModel):
    """Coupled function meaning and missing-context publication contract."""

    model_config = ConfigDict(extra="forbid")

    function_id: str = Field(..., min_length=1)
    meaning_status: MeaningStatus
    missing_context_items: tuple[MissingContextItem, ...] = ()

    @model_validator(mode="after")
    def _coupled_presence(self) -> "FunctionMeaningState":
        if self.meaning_status == "sufficient" and self.missing_context_items:
            raise ValueError("sufficient function meaning cannot have missing context")
        if self.meaning_status != "sufficient" and not self.missing_context_items:
            raise ValueError(
                f"{self.meaning_status} function meaning requires missing context"
            )
        return self


FUNCTION_MEANING_PRODUCT_PROPERTY_KEYS = frozenset({
    "summary",
    "stereotype",
    "meaning_status",
    "missing_context_count",
    "missing_context_artifact_hash",
})


def function_meaning_product_properties(function: object) -> dict[str, object]:
    """Return the closed, coupled product property slice for one routine."""
    function_id = str(getattr(function, "id", "") or "").strip()
    label = str(getattr(function, "label", "") or "")
    if not function_id:
        raise ValueError("function meaning owner has a blank stable ID")
    if label not in ROUTINE_LABELS:
        raise ValueError(
            f"non-routine CodeNode cannot publish function meaning: {function_id}"
        )
    status = str(getattr(function, "meaning_status", "") or "")
    if status not in {"sufficient", "partial", "insufficient"}:
        raise ValueError(
            f"{function_id}: invalid or missing function meaning_status: {status!r}"
        )
    count = getattr(function, "missing_context_count", None)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError(f"{function_id}: invalid missing_context_count: {count!r}")
    summary = str(getattr(function, "summary", "") or "").strip()
    stereotype = str(getattr(function, "stereotype", "") or "").strip()
    artifact_hash = str(
        getattr(function, "missing_context_artifact_hash", "") or ""
    )
    if bool(summary) != bool(stereotype):
        raise ValueError(f"{function_id}: summary/stereotype presence differs")
    if status == "sufficient":
        if not summary or count != 0 or artifact_hash:
            raise ValueError(
                f"{function_id}: sufficient meaning property presence is invalid"
            )
    elif status == "partial":
        if not summary or count < 1:
            raise ValueError(
                f"{function_id}: partial meaning property presence is invalid"
            )
    elif summary or count < 1:
        raise ValueError(
            f"{function_id}: insufficient meaning property presence is invalid"
        )
    if count:
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_hash):
            raise ValueError(
                f"{function_id}: missing-context artifact hash is invalid"
            )
    elif artifact_hash:
        raise ValueError(
            f"{function_id}: zero missing-context count cannot have artifact hash"
        )

    properties: dict[str, object] = {
        "meaning_status": status,
        "missing_context_count": count,
    }
    if summary:
        properties["summary"] = summary
        properties["stereotype"] = stereotype
    if artifact_hash:
        properties["missing_context_artifact_hash"] = artifact_hash
    return properties


def derive_function_meaning_state(
    *,
    function_id: str,
    summary: str | None,
    stereotype: str | None,
    summary_frame: SemanticFrame | None,
    findings: Iterable[AnalysisFinding] = (),
) -> FunctionMeaningState:
    """Derive one final state without asking the model to repeat provenance.

    A semantic slot is the stable identity of the meaning obligation that remains
    open.  It is therefore the missing-context reference; raw request-local keys and
    prose-only model explanations never become identity.
    """
    normalized_summary = str(summary or "").strip()
    normalized_stereotype = str(stereotype or "").strip()
    if bool(normalized_summary) != bool(normalized_stereotype):
        raise ValueError(
            f"{function_id}: summary and stereotype must be present or absent together"
        )

    summary_slots = []
    if summary_frame is not None:
        if summary_frame.target.id != function_id:
            raise ValueError(
                f"{function_id}: summary frame belongs to {summary_frame.target.id}"
            )
        summary_slots = sorted(
            (
                slot
                for slot in summary_frame.slots.values()
                if (
                    (slot.family == "FUNCTION" and slot.role in FUNCTION_SUMMARY_ROLES)
                    or (slot.family, slot.role)
                    in {("STATEMENT", "DIRECT_ACTION"), ("CALL", "PURPOSE")}
                )
            ),
            key=lambda slot: slot.slot_id,
        )

    items: list[MissingContextItem] = []
    for slot in summary_slots:
        if slot.status == "sufficient":
            continue
        description = "; ".join(
            dict.fromkeys(
                " ".join(str(reason).split())
                for reason in slot.missing_context
                if str(reason).strip()
            )
        )
        if not description:
            raise ValueError(
                f"{function_id}: non-sufficient summary slot lacks missing context: "
                f"{slot.slot_id}"
            )
        items.append(MissingContextItem(
            missing_context_ref=slot.slot_id,
            reason_code=f"summary_slot_{slot.status}",
            description=description,
        ))

    if normalized_summary:
        if summary_frame is None:
            raise ValueError(f"{function_id}: mapped summary lacks its closed semantic frame")
        meaning_status: MeaningStatus = "partial" if items else "sufficient"
    else:
        meaning_status = "insufficient"
        if not items:
            summary_findings = [
                finding
                for finding in findings
                if finding.concern in {"summary", "semantic_frame"}
            ]
            for finding in summary_findings:
                descriptions = tuple(
                    dict.fromkeys(
                        " ".join(str(reason).split())
                        for reason in finding.reasons
                        if str(reason).strip()
                    )
                )
                description = "; ".join(descriptions)
                if not description:
                    description = f"{finding.concern} {finding.status}"
                items.append(MissingContextItem(
                    missing_context_ref=finding.target_id or function_id,
                    reason_code=f"{finding.concern}_{finding.status}",
                    description=description,
                ))
        if not items:
            raise ValueError(
                f"{function_id}: insufficient meaning lacks a concrete missing-context cause"
            )

    return FunctionMeaningState(
        function_id=function_id,
        meaning_status=meaning_status,
        missing_context_items=tuple(items),
    )
