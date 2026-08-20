"""Canonical SQL identifier identity shared by parser-owned projections.

Quoted identifiers retain their exact inner spelling. Unquoted identifiers use the SQL
case-insensitive canonical form. The kind prefix is intentional: consumers must not erase
whether the grammar saw quotes and silently fall back to string case folding.
"""
from __future__ import annotations

from typing import Literal, TypeAlias


SqlIdentifierKey: TypeAlias = tuple[Literal["Q", "U"], str]


def sql_identifier_key(value: object, quoted: bool) -> SqlIdentifierKey:
    """Return ``("Q", exact)`` or ``("U", UPPERCASE)`` for one identifier."""
    text = str(value or "").strip()
    if quoted:
        if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
            text = text[1:-1].replace('""', '"')
        return "Q", text
    return "U", text.upper()
