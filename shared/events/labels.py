"""Human-readable observability labels."""
from __future__ import annotations


def label_or_raw(value: str, labels: dict[str, str]) -> str:
    """Resolve a display label while preserving unknown machine-readable names."""
    return labels.get(value, value)
