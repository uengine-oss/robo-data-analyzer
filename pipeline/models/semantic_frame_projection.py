"""Validated semantic-frame projection into the published code summary."""
from __future__ import annotations

from collections.abc import Iterable

from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.models.semantic_frame import (
    FUNCTION_SUMMARY_ROLES,
    SemanticFrame,
    SemanticSlot,
)


_SUMMARY_ROLE_ORDER = {
    **{
        ("FUNCTION", role): index
        for index, role in enumerate(FUNCTION_SUMMARY_ROLES)
    },
    ("STATEMENT", "DIRECT_ACTION"): 0,
    ("CALL", "PURPOSE"): 0,
}


def project_summary(
    frame: SemanticFrame,
    *,
    summary: str,
    stereotype: str,
) -> SummaryOutput:
    """Publish the one model-owned whole-target summary with its validated frame.

    Slot meanings are structured semantic details.  Joining those details does not
    produce a readable whole-target summary and used to leak the response schema into
    the product text.  The same model response therefore owns exactly one final
    ``summary``; this projection only validates and attaches deterministic metadata.
    """
    candidates = [
        slot for slot in frame.slots.values()
        if (slot.family, slot.role) in _SUMMARY_ROLE_ORDER
    ]
    def _source_order(slot: SemanticSlot) -> tuple[int, int, str]:
        coordinates = [
            (fact.start_line, fact.end_line)
            for ref in slot.evidence_refs
            for fact in (frame.profile.evidence[ref],)
            if fact.kind != "TARGET" and fact.start_line > 0
        ]
        if not coordinates:
            return (2**31, 2**31, slot.slot_id)
        start, end = min(coordinates)
        return (start, end, slot.slot_id)

    candidates.sort(key=lambda slot: (
        _SUMMARY_ROLE_ORDER[(slot.family, slot.role)], *_source_order(slot),
    ))
    grounded = [slot for slot in candidates if slot.meaning.strip()]
    missing = _dedupe_meanings(
        item
        for slot in candidates
        for item in slot.missing_context
        if item.strip()
    )

    if candidates and all(slot.status == "sufficient" for slot in candidates):
        status = "sufficient"
    elif grounded:
        status = "partial"
        if not missing:
            # A non-sufficient leaf is invalid without missing_context, but preserve an
            # explicit invariant here so projection cannot silently manufacture validity.
            raise ValueError("partial semantic frame projection lacks missing_context")
    else:
        status = "insufficient"
        if not missing:
            raise ValueError("insufficient semantic frame projection lacks missing_context")

    return SummaryOutput(
        summary=summary,
        stereotype=stereotype if status != "insufficient" else "",
        evidence_status=status,
        missing_context=missing,
        semantic_frame=frame,
    )


def _dedupe_meanings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(str(raw).split())
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
