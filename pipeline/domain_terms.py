"""Bounded domain-term normalization and Neo4j-safe JSON codec (spec 050)."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from shared.log.logger import log_warning

MAX_DOMAIN_TERMS = 10
_ASCII_CODE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TECHNICAL_NAMES = {"NULL", "TRUE", "FALSE", "OK", "ERROR", "FAIL", "SUCCESS"}


def _looks_like_code_identifier(term: str) -> bool:
    """Reject implementation names while allowing compact domain acronyms such as PG/VAN."""
    if not _ASCII_CODE_IDENTIFIER.fullmatch(term):
        return False
    if term in _TECHNICAL_NAMES:
        return True
    # Uppercase acronym without separators can be a genuine domain headword. Semantic
    # relevance remains the prompt's job; snake/camel/lower identifiers are never glossary keys.
    return not (term.isupper() and "_" not in term and 2 <= len(term) <= 12)


def normalize_domain_terms(
    items: Iterable[Any], *, source_text: str, code_id: str, component: str
) -> dict[str, str]:
    """Keep valid, source-grounded terms in model priority order; never silently drop."""
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

    dropped = {reason: count for reason, count in dropped.items() if count}
    if dropped:
        log_warning(
            component,
            "domain_terms_dropped",
            "invalid or duplicate domain terms were removed",
            code_id=code_id,
            dropped=dropped,
            kept=len(result),
        )
    return result


def dump_domain_terms(terms: Mapping[str, str]) -> str:
    """Canonical JSON object for a Neo4j scalar property."""
    validate_domain_terms(terms)
    return json.dumps(dict(terms), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_domain_terms(raw: str) -> dict[str, str]:
    """Strictly decode the spec-050 graph property; legacy delimiter text is rejected."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("domain_terms_json must be a JSON object")
    validate_domain_terms(value)
    return dict(value)


def validate_domain_terms(terms: Mapping[Any, Any]) -> None:
    if len(terms) > MAX_DOMAIN_TERMS:
        raise ValueError(f"domain terms exceed maximum {MAX_DOMAIN_TERMS}")
    for term, meaning in terms.items():
        if not isinstance(term, str) or not isinstance(meaning, str):
            raise ValueError("domain term keys and meanings must be strings")
        if not term.strip() or not meaning.strip() or len(term.strip()) > 60:
            raise ValueError("domain terms must be non-empty and term length <= 60")
