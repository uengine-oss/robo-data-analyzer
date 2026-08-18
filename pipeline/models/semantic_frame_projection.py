"""Compatibility projections derived solely from validated semantic frames."""
from __future__ import annotations

from collections.abc import Iterable

from pipeline.models.code_analysis_output import QuestionItem, SummaryOutput
from pipeline.models.semantic_frame import SemanticFrame, SemanticSlot


_SUMMARY_ROLE_ORDER = {
    ("FUNCTION", "RESPONSIBILITY"): 0,
    ("FUNCTION", "INPUT_ROLE"): 1,
    ("FUNCTION", "RESULT"): 2,
    ("STATEMENT", "DIRECT_ACTION"): 0,
}


def project_summary(frame: SemanticFrame, *, stereotype: str) -> SummaryOutput:
    """Project the old code summary contract without another meaning producer."""
    candidates = [
        slot for slot in frame.slots.values()
        if (slot.family, slot.role) in _SUMMARY_ROLE_ORDER
    ]
    candidates.sort(key=lambda slot: (
        _SUMMARY_ROLE_ORDER[(slot.family, slot.role)], slot.slot_id,
    ))
    grounded = [slot for slot in candidates if slot.meaning.strip()]
    meanings = _dedupe_meanings(slot.meaning for slot in grounded)
    missing = _dedupe_meanings(
        item
        for slot in candidates
        for item in slot.missing_context
        if item.strip()
    )[:8]

    if candidates and all(slot.status == "sufficient" for slot in candidates):
        status = "sufficient"
    elif meanings:
        status = "partial"
        if not missing:
            # A non-sufficient leaf is invalid without missing_context, but preserve an
            # explicit invariant here so projection cannot silently manufacture validity.
            raise ValueError("partial semantic frame projection lacks missing_context")
    else:
        status = "insufficient"
        if not missing:
            raise ValueError("insufficient semantic frame projection lacks missing_context")

    questions = _questions(candidates)[:15]
    return SummaryOutput(
        summary="\n".join(meanings),
        stereotype=stereotype if status != "insufficient" else "",
        evidence_status=status,
        missing_context=missing,
        questions=questions,
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


def _questions(slots: Iterable[SemanticSlot]) -> list[QuestionItem]:
    result: list[QuestionItem] = []
    seen: set[tuple[str, str]] = set()
    for slot in slots:
        for missing in slot.missing_context:
            text = " ".join(missing.split())
            reason = f"{slot.family}.{slot.role} slot의 현재 근거가 부족함"
            key = (text.casefold(), reason)
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(QuestionItem(text=text, reason=reason))
    return result


_RULE_ROLE_ORDER = {
    ("RULE", "BUSINESS_MEANING"): 0,
}


def project_rule_narrative(frame: SemanticFrame, anchor_line: int) -> str:
    slots = []
    for slot in frame.slots.values():
        anchors = {
            int(fact.attributes["anchor_line"])
            for ref in slot.evidence_refs
            if (fact := frame.profile.evidence[ref]).kind == "RULE"
            and fact.attributes.get("anchor_line") is not None
        }
        if anchor_line in anchors:
            slots.append(slot)
    slots.sort(key=lambda slot: (
        _RULE_ROLE_ORDER.get((slot.family, slot.role), 99), slot.slot_id,
    ))
    meanings = _dedupe_meanings(slot.meaning for slot in slots if slot.meaning.strip())
    if not meanings:
        missing = [item for slot in slots for item in slot.missing_context]
        if missing:
            return "현재 근거만으로 이 경로의 의미를 확정할 수 없다."
        raise ValueError(f"rule row lacks semantic meaning: {anchor_line}")
    narrative = " ".join(meanings)
    return narrative
