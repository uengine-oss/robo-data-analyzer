"""Render one human/search document from source-owned semantic coordinates.

The renderer never interprets literals or identifier spelling.  Natural-language purpose is
the only model-owned input; every branch, call, data access, and return coordinate is copied
from deterministic analysis structures.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from pipeline.models.code_analysis_output import RuleItem


def _ordered(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def render_semantic_document(
    *,
    purpose: str,
    signature: str,
    rules: Sequence[RuleItem],
    callees: Iterable[str] = (),
    reads: Iterable[str] = (),
    writes: Iterable[str] = (),
    returns: Iterable[str] = (),
) -> str:
    """Build the display and embedding source without guessing source-code meaning."""
    normalized_purpose = (purpose or "").strip()
    if not normalized_purpose:
        raise ValueError("semantic purpose is empty")
    normalized_signature = (signature or "").strip()
    if not normalized_signature:
        raise ValueError("semantic signature is empty")

    lines = [
        f"[PURPOSE] {normalized_purpose}",
        f"[SIGNATURE] {normalized_signature}",
        "[RULES]",
    ]
    for rule in sorted(rules, key=lambda value: value.anchor_line):
        condition = rule.cond if rule.cond is not None else "ALWAYS"
        effects = " ; ".join(rule.then) if rule.then else "NO_DIRECT_EFFECT"
        lines.append(f"- R{rule.anchor_line}: IF {condition} => {effects}")

    for heading, values in (
        ("CALLS", callees),
        ("READS", reads),
        ("WRITES", writes),
        ("RETURNS", returns),
    ):
        ordered = _ordered(values)
        lines.append(f"[{heading}] " + (" ; ".join(ordered) if ordered else "NONE"))
    return "\n".join(lines)
