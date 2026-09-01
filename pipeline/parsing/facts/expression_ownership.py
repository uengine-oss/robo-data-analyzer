"""Assign resolved expression facts to their nearest structural routine owner."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from graph.schema.graph_nodes import CodeNode
from pipeline.source_languages import ROUTINE_LABELS


class ExpressionOwnershipError(ValueError):
    """An expression fact cannot be assigned exactly once to a routine."""


def _owning_routine(
    node: CodeNode,
    code_by_id: Mapping[str, CodeNode],
) -> CodeNode:
    current = node
    seen: set[str] = set()
    while current.label not in ROUTINE_LABELS:
        if current.id in seen:
            raise ExpressionOwnershipError(
                f"expression owner parent cycle at {current.id!r}"
            )
        seen.add(current.id)
        if not current.parent_id:
            raise ExpressionOwnershipError(
                "expression owner has no routine ancestor: "
                f"{node.id!r} ({node.file_path}:{node.start_line})"
            )
        parent = code_by_id.get(current.parent_id)
        if parent is None:
            raise ExpressionOwnershipError(
                f"expression owner parent is missing: {current.id!r} → "
                f"{current.parent_id!r}"
            )
        current = parent
    return current


def routine_expression_ledger(
    code_nodes: Sequence[CodeNode],
    facts: Sequence[dict],
) -> dict[str, tuple[dict, ...]]:
    """Return every resolved expression exactly once under its nearest routine."""
    code_by_id = {node.id: node for node in code_nodes}
    if len(code_by_id) != len(code_nodes):
        raise ExpressionOwnershipError("expression ownership requires unique CodeNode IDs")
    result: dict[str, list[dict]] = {
        node.id: [] for node in code_nodes if node.label in ROUTINE_LABELS
    }
    fact_ids: list[str] = []
    for fact in facts:
        fact_id = str(fact.get("fact_id") or "")
        code_node_id = str(fact.get("code_node_id") or "")
        if not fact_id or not code_node_id:
            raise ExpressionOwnershipError(
                "resolved expression requires fact_id and code_node_id"
            )
        node = code_by_id.get(code_node_id)
        if node is None:
            raise ExpressionOwnershipError(
                f"resolved expression owner is missing: {fact_id!r} → {code_node_id!r}"
            )
        routine = _owning_routine(node, code_by_id)
        result[routine.id].append(fact)
        fact_ids.append(fact_id)
    if len(fact_ids) != len(set(fact_ids)):
        raise ExpressionOwnershipError("resolved expression fact IDs are not exact-once")

    def order(fact: dict) -> tuple[int, int, str]:
        source_range = fact.get("source_range") or {}
        return (
            int(source_range.get("char_offset") or 0),
            int(source_range.get("char_length") or 0),
            str(fact.get("fact_id") or ""),
        )

    ledger = {
        routine_id: tuple(sorted(items, key=order))
        for routine_id, items in result.items()
    }
    assigned = [
        str(fact["fact_id"])
        for items in ledger.values()
        for fact in items
    ]
    if len(assigned) != len(fact_ids) or set(assigned) != set(fact_ids):
        raise ExpressionOwnershipError("routine expression ledger lost or duplicated facts")
    return ledger


def attach_routine_expression_ledger(
    code_nodes: Sequence[CodeNode],
    facts: Sequence[dict],
) -> dict[str, tuple[dict, ...]]:
    """Attach the internal ledger without persisting it as graph node properties."""
    ledger = routine_expression_ledger(code_nodes, facts)
    by_id = {node.id: node for node in code_nodes}
    for routine_id, items in ledger.items():
        routine = by_id[routine_id]
        if "_resolved_expression_facts" in routine.extra:
            raise ExpressionOwnershipError(
                f"routine expression ledger already attached: {routine_id!r}"
            )
        routine.extra["_resolved_expression_facts"] = items
    return ledger
