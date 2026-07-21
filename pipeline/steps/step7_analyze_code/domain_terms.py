"""Bound and source-ground domain terms produced during code analysis."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from shared.events.runtime_logger import log_warning

MAX_DOMAIN_TERMS = 10
_ASCII_CODE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TECHNICAL_NAMES = {"NULL", "TRUE", "FALSE", "OK", "ERROR", "FAIL", "SUCCESS"}


def _looks_like_code_identifier(term: str) -> bool:
    if not _ASCII_CODE_IDENTIFIER.fullmatch(term):
        return False
    if term in _TECHNICAL_NAMES:
        return True
    return not (term.isupper() and "_" not in term and 2 <= len(term) <= 12)


def normalize_domain_terms(
    items: Iterable[Any], *, source_text: str, code_id: str, component: str
) -> dict[str, str]:
    """Keep valid source-grounded terms in model priority order."""
    items = list(items)
    if len(items) > MAX_DOMAIN_TERMS:
        raise ValueError(f"domain terms exceed maximum {MAX_DOMAIN_TERMS}")
    result: dict[str, str] = {}
    seen: set[str] = set()
    dropped = {
        "empty": 0,
        "too_long": 0,
        "code_identifier": 0,
        "not_in_source": 0,
        "duplicate": 0,
    }
    for item in items:
        term = str(getattr(item, "term", "")).strip()
        meaning = str(getattr(item, "meaning", "")).strip()
        if not term or not meaning:
            dropped["empty"] += 1
            continue
        if len(term) > 60:
            dropped["too_long"] += 1
            continue
        if _looks_like_code_identifier(term):
            dropped["code_identifier"] += 1
            continue
        if term not in source_text:
            dropped["not_in_source"] += 1
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
