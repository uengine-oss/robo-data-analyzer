"""Literal constant facts for deterministic external linking.

This is deliberately not value tracking.  It accepts only a Constant whose
declaration value is one complete quoted literal and a unique REFERENCES edge
from the routine to that constant.  Local assignments, mutable variables,
concatenation, control flow, and recursive INIT_BY evaluation are outside the
contract.
"""
from __future__ import annotations

from collections import defaultdict

from graph.schema.graph_edges import References
from graph.schema.graph_nodes import Constant
class LiteralConstantIndex:
    """Resolve a referenced name only when one literal declaration proves it."""

    def __init__(
        self,
        constants: list[Constant],
        references: list[References] | None = None,
    ) -> None:
        literal_by_id: dict[str, str] = {}
        name_by_id: dict[str, str] = {}
        for constant in constants:
            literal = _complete_literal(constant.value)
            if literal is not None:
                literal_by_id[constant.id] = literal
                name_by_id[constant.id] = constant.name

        candidates: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for edge in references or ():
            name = name_by_id.get(edge.target_id)
            if name is not None:
                candidates[edge.source_id][name].add(edge.target_id)

        self._values: dict[str, dict[str, str]] = {}
        for routine_id, by_name in candidates.items():
            resolved: dict[str, str] = {}
            for name, node_ids in by_name.items():
                if len(node_ids) == 1:
                    resolved[name] = literal_by_id[next(iter(node_ids))]
            self._values[routine_id] = resolved

    def value_for(self, routine_id: str, name: str) -> str | None:
        return self._values.get(routine_id, {}).get(name)


def _complete_literal(expression: str) -> str | None:
    return decode_complete_string_literal(expression)


def decode_complete_string_literal(expression: str) -> str | None:
    """Decode only a complete literal argument selected by Parser structure."""
    raw = expression.strip()
    if not raw:
        return None
    pieces: list[str] = []
    cursor = 0
    while cursor < len(raw):
        while cursor < len(raw) and raw[cursor].isspace():
            cursor += 1
        prefix_start = cursor
        while cursor < len(raw) and raw[cursor] not in {"'", '"'}:
            if not (raw[cursor].isalnum() or raw[cursor] == "_"):
                return None
            cursor += 1
        if cursor >= len(raw):
            return None
        prefix = raw[prefix_start:cursor].lower()
        if any(char not in "rubf8l" for char in prefix) or "f" in prefix:
            return None
        raw_mode = "r" in prefix
        quote = raw[cursor]
        cursor += 1
        value: list[str] = []
        closed = False
        while cursor < len(raw):
            char = raw[cursor]
            if char == quote:
                if cursor + 1 < len(raw) and raw[cursor + 1] == quote:
                    value.append(quote)
                    cursor += 2
                    continue
                cursor += 1
                closed = True
                break
            if char != "\\" or raw_mode:
                value.append(char)
                cursor += 1
                continue
            decoded, cursor = _decode_escape(raw, cursor)
            if decoded is None:
                return None
            value.append(decoded)
        if not closed:
            return None
        pieces.append("".join(value))
        while cursor < len(raw) and raw[cursor].isspace():
            cursor += 1
        if cursor < len(raw) and raw[cursor] not in {"'", '"'} and not raw[cursor].isalnum():
            return None
    value = "".join(pieces)
    return value if value else None


def _decode_escape(raw: str, slash: int) -> tuple[str | None, int]:
    if slash + 1 >= len(raw):
        return None, len(raw)
    marker = raw[slash + 1]
    simple = {
        "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
        "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"',
    }
    if marker in simple:
        return simple[marker], slash + 2
    if marker == "\n":
        return "", slash + 2
    widths = {"x": 2, "u": 4, "U": 8}
    if marker in widths:
        width = widths[marker]
        digits = raw[slash + 2:slash + 2 + width]
        if len(digits) != width or any(char not in "0123456789abcdefABCDEF" for char in digits):
            return None, slash + 2
        try:
            return chr(int(digits, 16)), slash + 2 + width
        except ValueError:
            return None, slash + 2 + width
    if marker in "01234567":
        end = slash + 2
        while end < min(len(raw), slash + 4) and raw[end] in "01234567":
            end += 1
        return chr(int(raw[slash + 1:end], 8)), end
    return None, slash + 2
