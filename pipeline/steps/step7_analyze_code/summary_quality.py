"""Framework/DBMS가 공유하는 표시·검색 텍스트 경계."""
from __future__ import annotations


def normalize_canonical_summary(summary: str) -> str:
    """Normalize the one model-owned text reused for display, search, and handoff."""
    normalized = (summary or "").strip()
    if not normalized:
        raise ValueError("summary is empty")
    return normalized
