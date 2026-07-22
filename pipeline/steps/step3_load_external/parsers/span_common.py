"""Shared span-adapter contract (spec 078).

Every text-format adapter maps a decoded source string to ordered
``SemanticSpan`` items.  Adapters never re-serialize: the chunk text is always
``source[char_start:char_end]``, sliced by the caller after verification.
Malformed input raises ``AdapterParseError`` — fail-closed, never a partial
success.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


class AdapterParseError(ValueError):
    """The file cannot be split into verified semantic spans."""


@dataclass(frozen=True)
class SemanticSpan:
    char_start: int
    char_end: int
    keys: tuple[str, ...]
    semantic_kind: str
    name: str = ""


_KEY_PATTERN = re.compile(r"^[\w가-힣\[][\w.\-\[\]가-힣]*$")


def is_reference_key(value: str) -> bool:
    """Exact-match reference keys: identifier-like, length >= 2, not a URL/path."""
    return bool(
        len(value) >= 2
        and not value.startswith(("http", "/"))
        and _KEY_PATTERN.fullmatch(value)
    )


def line_offsets(source: str) -> list[int]:
    """Cumulative char offset of each line start; last entry == len(source)."""
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def extend_through_eol(source: str, end: int) -> int:
    """Extend ``end`` over trailing spaces/tabs and one newline, if present."""
    length = len(source)
    while end < length and source[end] in (" ", "\t"):
        end += 1
    if source.startswith("\r\n", end):
        return end + 2
    if end < length and source[end] == "\n":
        return end + 1
    return end


def sorted_unique_keys(keys: list[str]) -> tuple[str, ...]:
    return tuple(sorted({key for key in keys if is_reference_key(key)}))
