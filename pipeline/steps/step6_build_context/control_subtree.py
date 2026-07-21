"""Build routine-relative control trees from the deterministic AST graph."""
from __future__ import annotations

from collections import defaultdict

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import ControlSubtreeNode
from pipeline.languages import ROUTINE_LABELS, control_structure_labels


def build_control_subtrees(
    code_nodes: list[CodeNode],
) -> dict[str, list[ControlSubtreeNode]]:
    """Return control trees keyed by owning routine id.

    The reader already persists structural identity and absolute source ranges.
    This boundary converts only coordinates: a control at the routine's first
    physical line becomes line 1 in the routine ``code_text`` contract.
    """
    controls = control_structure_labels()
    children_by_parent: dict[str, list[CodeNode]] = defaultdict(list)
    for node in code_nodes:
        if node.label in controls and node.parent_id:
            children_by_parent[node.parent_id].append(node)
    for children in children_by_parent.values():
        children.sort(key=lambda node: (node.start_line, node.end_line, node.id))

    def convert(node: CodeNode, routine: CodeNode) -> ControlSubtreeNode:
        start = node.start_line - routine.start_line + 1
        end = node.end_line - routine.start_line + 1
        if start < 1 or end < start or node.end_line > routine.end_line:
            raise ValueError(
                "control range escapes routine: "
                f"control={node.id!r} [{node.start_line},{node.end_line}] "
                f"routine={routine.id!r} [{routine.start_line},{routine.end_line}]"
            )
        return ControlSubtreeNode(
            node_id=node.id,
            ntype=node.label,
            start=start,
            end=end,
            children=[convert(child, routine) for child in children_by_parent.get(node.id, ())],
        )

    result: dict[str, list[ControlSubtreeNode]] = {}
    for routine in code_nodes:
        if routine.label not in ROUTINE_LABELS:
            continue
        roots = children_by_parent.get(routine.id, ())
        if roots:
            result[routine.id] = [convert(root, routine) for root in roots]

    return result
