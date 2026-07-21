"""Exact quoted-key indexing over already-loaded CodeNode text.

This is a lexical boundary, not a source parser: it recognizes strings and
comments only, so partial substrings and comment examples never become facts.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class LiteralOccurrence:
    value: str
    line: int
    start: int
    end: int


def _unescape(value: str) -> str:
    output: list[str] = []
    index = 0
    simple = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "b": "\b", "\\": "\\", "\"": "\"", "'": "'"}
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            output.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        if escaped == "u" and index + 5 < len(value):
            digits = value[index + 2:index + 6]
            if re.fullmatch(r"[0-9A-Fa-f]{4}", digits):
                output.append(chr(int(digits, 16)))
                index += 6
                continue
        output.append(simple.get(escaped, escaped))
        index += 2
    return "".join(output)


def quoted_literals(text: str) -> list[LiteralOccurrence]:
    """Return single/double quoted literals outside comments and triple strings."""
    result: list[LiteralOccurrence] = []
    index = 0
    line = 1
    length = len(text)
    while index < length:
        if text.startswith("//", index) or text.startswith("--", index):
            end = text.find("\n", index + 2)
            if end < 0:
                break
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                break
            line += text.count("\n", index, end + 2)
            index = end + 2
            continue
        if text[index] == "#":
            end = text.find("\n", index + 1)
            if end < 0:
                break
            index = end
            continue
        if text.startswith(('"""', "'''"), index):
            marker = text[index:index + 3]
            end = text.find(marker, index + 3)
            if end < 0:
                break
            line += text.count("\n", index, end + 3)
            index = end + 3
            continue
        quote = text[index]
        if quote not in ("\"", "'"):
            if quote == "\n":
                line += 1
            index += 1
            continue
        start = index
        start_line = line
        index += 1
        raw: list[str] = []
        while index < length:
            char = text[index]
            if char == "\\" and index + 1 < length:
                raw.extend((char, text[index + 1]))
                if text[index + 1] == "\n":
                    line += 1
                index += 2
                continue
            if char == quote:
                index += 1
                result.append(LiteralOccurrence(_unescape("".join(raw)), start_line, start, index))
                break
            if char == "\n":
                line += 1
            raw.append(char)
            index += 1
        else:
            break
    return result


def match_keys(text: str, keys: Iterable[str]) -> list[tuple[str, int]]:
    """O(literals + matches) exact lookup; result equals exhaustive comparison."""
    key_set = {key for key in keys if key}
    seen: set[str] = set()
    output: list[tuple[str, int]] = []
    for occurrence in quoted_literals(text):
        if occurrence.value in key_set and occurrence.value not in seen:
            seen.add(occurrence.value)
            output.append((occurrence.value, occurrence.line))
    return output


def match_keys_exhaustive(text: str, keys: Iterable[str]) -> list[tuple[str, int]]:
    """Reference implementation retained for parity tests, not production use."""
    occurrences = quoted_literals(text)
    output: list[tuple[str, int]] = []
    for key in keys:
        if not key:
            continue
        match = next((occurrence for occurrence in occurrences if occurrence.value == key), None)
        if match is not None:
            output.append((key, match.line))
    return output


def mask_comments_and_literals(text: str) -> str:
    """Preserve length/newlines while blanking comments and literal bodies."""
    chars = list(text)
    triple = re.compile(r'(?s)(""".*?"""|\'\'\'.*?\'\'\')')
    for match in triple.finditer(text):
        for index in range(match.start(), match.end()):
            if chars[index] != "\n":
                chars[index] = " "
    spans = [(item.start, item.end) for item in quoted_literals(text)]
    for start, end in spans:
        for index in range(start, end):
            if chars[index] != "\n":
                chars[index] = " "
    index = 0
    while index < len(chars):
        if text.startswith("//", index) or text.startswith("--", index) or text[index:index + 1] == "#":
            end = text.find("\n", index + 1)
            end = len(text) if end < 0 else end
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            end = len(text) if close < 0 else close + 2
        else:
            index += 1
            continue
        for position in range(index, end):
            if chars[position] != "\n":
                chars[position] = " "
        index = end
    return "".join(chars)


def dynamic_key_candidates(text: str, keys: Iterable[str]) -> list[str]:
    """Return known keys whose literal fragment participates in ``+`` assembly."""
    known = sorted({key for key in keys if key})
    suspected: set[str] = set()
    for occurrence in quoted_literals(text):
        fragment = occurrence.value
        if len(fragment) < 3:
            continue
        before = text[max(0, occurrence.start - 80):occurrence.start]
        after = text[occurrence.end:occurrence.end + 80]
        concatenated = bool(
            re.search(r"[A-Za-z_]\w*\s*\+\s*$", before)
            or re.match(r"\s*\+\s*[A-Za-z_]\w*", after)
        )
        if not concatenated:
            continue
        suspected.update(key for key in known if fragment != key and fragment in key)
    return sorted(suspected)
