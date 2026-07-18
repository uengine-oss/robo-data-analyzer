"""Neo4j-safe scalar codec for domain-term mappings."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

MAX_DOMAIN_TERMS = 10


def dump_domain_terms(terms: Mapping[str, str]) -> str:
    """Encode a validated mapping as canonical JSON."""
    validate_domain_terms(terms)
    return json.dumps(dict(terms), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_domain_terms(raw: str) -> dict[str, str]:
    """Strictly decode the graph property; legacy delimiter text is rejected."""
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
