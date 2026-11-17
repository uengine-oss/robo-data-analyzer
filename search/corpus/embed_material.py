"""Canonical text used for routine semantic search."""
from __future__ import annotations

import json


def compose_embed_text(row: dict) -> str:
    """Return the validated routine summary without adding duplicate signals."""
    return (row.get("summary") or "").strip()


def as_vector(value) -> list[float]:
    """저장된 검색 벡터 → float 목록.

    Ontological DB 는 벡터 속성을 ``"[0.1, …]"`` 문자열로 돌려준다(59ecb4f8 실측). Neo4j 는 목록.
    """
    if isinstance(value, str):
        value = json.loads(value)
    return [float(x) for x in value]
