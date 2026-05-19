"""스키마 결정 위계 — own_schema vs explicit vs default.

규칙:
1. ``explicit_schema`` (LLM / DDL 명시) 있으면 그대로 신뢰.
2. 없으면 ``own_schema`` 사용 (PROCEDURE 자기 스키마 — DBMS only).
3. 둘 다 없으면 빈 문자열 — 강제 'public' 박지 X
   (catalog miss = 정상 fallback).
"""
from __future__ import annotations


def resolve_schema(explicit_schema: str = "", own_schema: str = "") -> str:
    """위계: explicit > own > "".

    Args:
        explicit_schema: LLM / 코드 명시 스키마 (최우선).
        own_schema: PROCEDURE 자기 schema (DBMS only).

    Returns:
        결정된 스키마. 둘 다 없으면 빈 문자열.
    """
    if explicit_schema:
        return explicit_schema
    if own_schema:
        return own_schema
    return ""
