"""Canonical text used for routine semantic search."""
from __future__ import annotations


def compose_embed_text(row: dict) -> str:
    """Return the validated routine summary without adding duplicate signals."""
    return (row.get("summary") or "").strip()
