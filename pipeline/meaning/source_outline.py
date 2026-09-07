"""Compact parser-owned syntax outline for model input."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from graph.schema.graph_nodes import CodeNode
from pipeline.source_languages import (
    callsite_labels,
    control_structure_labels,
    statement_labels,
)


_FRAMEWORK_CALL_LABELS = frozenset({"FUNCTION_CALL", "NEW_INSTANCE"})


def render_source_outline(
    target: CodeNode,
    code_by_id: Mapping[str, CodeNode],
) -> str:
    """Render exact AST ancestry as ``line range : syntax type`` lines.

    IDs, expressions, character offsets and semantic attributes are excluded. An
    unshown parser container is traversed without adding indentation, so every
    displayed level still means an actual ancestor rather than range inference.
    """
    visible_labels = (
        control_structure_labels()
        | statement_labels()
        | callsite_labels()
        | _FRAMEWORK_CALL_LABELS
    )
    children_by_parent: dict[str, list[CodeNode]] = defaultdict(list)
    for node in code_by_id.values():
        if node.id == target.id or node.parent_id is None:
            continue
        children_by_parent[node.parent_id].append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda node: (node.start_line, node.end_line, node.label, node.id))

    lines = [_outline_line(target, depth=0)]

    def visit(parent_id: str, depth: int) -> None:
        for child in children_by_parent.get(parent_id, ()):
            if not (
                target.start_line <= child.start_line <= child.end_line <= target.end_line
            ):
                continue
            shown = child.label in visible_labels
            if shown:
                lines.append(_outline_line(child, depth=depth))
            visit(child.id, depth + 1 if shown else depth)

    visit(target.id, 1)
    return "\n".join(lines)


def _outline_line(node: CodeNode, *, depth: int) -> str:
    span = (
        str(node.start_line)
        if node.start_line == node.end_line
        else f"{node.start_line}-{node.end_line}"
    )
    label = "CALL" if node.label in _FRAMEWORK_CALL_LABELS else node.label
    return f"{'  ' * depth}{span} : {label}"
