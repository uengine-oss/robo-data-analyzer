"""원문·결정론 이름 증거의 정확 일치 규칙.

창작 검사가 임의 부분문자열을 허용하지 않도록 식별자 경계와 완전한 정규화 조각만
비교한다. 언어별 이름 관습은 추측하지 않으며, 한정 이름은 결정론 생산자가 제공한
정확 ID 또는 마지막 구성요소와만 대조한다.
"""
from __future__ import annotations

import re
from typing import Iterable

_IDENT_CHAR = re.compile(r"[A-Za-z0-9_]")


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def source_fragment_present(fragment: str, source: str) -> bool:
    """공백만 접은 완전한 원문 조각이 식별자 경계에서 실재하는지 판정한다."""
    needle = compact_whitespace(fragment)
    compact_chars: list[str] = []
    source_positions: list[int] = []
    for position, character in enumerate(source):
        if character.isspace():
            continue
        compact_chars.append(character)
        source_positions.append(position)
    haystack = "".join(compact_chars)
    if not needle:
        return False
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return False
        original_start = source_positions[index]
        original_end = source_positions[index + len(needle) - 1]
        before = source[original_start - 1] if original_start else ""
        after_index = index + len(needle)
        after = source[original_end + 1] if original_end + 1 < len(source) else ""
        left_ok = not (
            _IDENT_CHAR.fullmatch(needle[0])
            and before
            and _IDENT_CHAR.fullmatch(before)
        )
        right_ok = not (
            _IDENT_CHAR.fullmatch(needle[-1])
            and after
            and _IDENT_CHAR.fullmatch(after)
        )
        if left_ok and right_ok:
            return True
        start = index + 1


def resolve_qualified_name(name: str, allowed: Iterable[str]) -> str | None:
    """정확 ID 또는 그 마지막 한정 구성요소와만 대조한다(부분문자열 금지)."""
    lowered = name.casefold()
    if not lowered:
        return None
    for raw in allowed:
        candidate = str(raw)
        if candidate.casefold() == lowered:
            return candidate
        leaf = candidate.split(":")[-1].split(".")[-1]
        if leaf.casefold() == lowered:
            return candidate
    return None
