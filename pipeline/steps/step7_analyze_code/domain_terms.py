"""Bound and source-ground domain terms produced during code analysis."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shared.events.runtime_logger import log_warning

MAX_DOMAIN_TERMS = 10


def normalize_domain_terms(
    items: Iterable[Any],
    *,
    source_text: str,
    numbered_source_text: str = "",
    code_id: str,
    component: str,
) -> dict[str, str]:
    """Keep valid source-grounded terms in model priority order.

    ``source_text`` is the canonical raw view used for deterministic term
    membership. ``numbered_source_text`` is the exact model-visible view of the
    same source range. Evidence may be copied from either representation; line
    prefixes are never guessed at or stripped.
    """
    items = list(items)
    if len(items) > MAX_DOMAIN_TERMS:
        raise ValueError(f"domain terms exceed maximum {MAX_DOMAIN_TERMS}")
    evidence_sources = tuple(
        text for text in (source_text, numbered_source_text) if text
    )
    result: dict[str, str] = {}
    seen: set[str] = set()
    dropped = {
        "empty": 0,
        "too_long": 0,
        "not_in_source": 0,
        "evidence_not_in_source": 0,
        "term_not_in_evidence": 0,
        "duplicate": 0,
    }
    for item in items:
        term = str(getattr(item, "term", "")).strip()
        meaning = str(getattr(item, "meaning", "")).strip()
        evidence = str(getattr(item, "evidence", "")).strip()
        if not term or not meaning or not evidence:
            dropped["empty"] += 1
            continue
        if len(term) > 60:
            dropped["too_long"] += 1
            continue
        if term not in source_text:
            dropped["not_in_source"] += 1
            continue
        if not any(evidence in candidate for candidate in evidence_sources):
            dropped["evidence_not_in_source"] += 1
            continue
        if term.casefold() not in evidence.casefold():
            dropped["term_not_in_evidence"] += 1
            continue
        folded = term.casefold()
        if folded in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(folded)
        result[term] = meaning
    rejected = {reason: count for reason, count in dropped.items() if count}
    if rejected:
        log_warning(
            component,
            "domain_terms_dropped",
            "invalid or duplicate domain terms were removed",
            code_id=code_id,
            dropped=rejected,
            kept=len(result),
        )
    return result
