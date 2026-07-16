"""Canonical node registration before graph persistence.

Parsers may observe one logical node more than once (for example, every file in a
folder observes the same Package). Identical observations are idempotent. Reusing
an id for a different concrete node remains a hard integrity failure.
"""
from __future__ import annotations

from typing import Iterable, TypeVar


_N = TypeVar("_N")


class NodeIdentityCollision(ValueError):
    """The same graph id was assigned to two different logical nodes."""


def _node_description(node: object) -> str:
    kind = type(node).__name__
    label = getattr(node, "label", "")
    name = getattr(node, "name", "")
    file_path = getattr(node, "file_path", "")
    start_line = getattr(node, "start_line", 0)
    details = [f"type={kind}"]
    if label:
        details.append(f"label={label}")
    if name:
        details.append(f"name={name}")
    if file_path:
        details.append(f"file={file_path}")
    if start_line:
        details.append(f"line={start_line}")
    return ", ".join(details)


def canonicalize_node_lists(
    *node_lists: Iterable[_N],
) -> tuple[tuple[list[_N], ...], int]:
    """Return ordered node lists with identical observations collapsed.

    Equality is intentionally strict: an observation is idempotent only when its
    concrete type and complete dataclass value match. Any differing value under an
    existing id is surfaced instead of being silently overwritten.
    """
    seen: dict[str, _N] = {}
    outputs: list[list[_N]] = []
    collapsed = 0

    for nodes in node_lists:
        output: list[_N] = []
        for node in nodes:
            node_id = getattr(node, "id", None)
            if not isinstance(node_id, str) or not node_id:
                raise NodeIdentityCollision(
                    f"node has no non-empty id ({_node_description(node)})"
                )

            existing = seen.get(node_id)
            if existing is None:
                seen[node_id] = node
                output.append(node)
                continue

            if type(existing) is type(node) and existing == node:
                collapsed += 1
                continue

            raise NodeIdentityCollision(
                f"node id collision {node_id!r}: "
                f"[{_node_description(existing)}] != [{_node_description(node)}]"
            )
        outputs.append(output)

    return tuple(outputs), collapsed
