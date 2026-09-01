"""Validation helpers for already-resolved user-routine ``CALLS`` edges."""
from __future__ import annotations

from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.source_languages import ROUTINE_LABELS


class VerifiedCallEdgeError(ValueError):
    """A product CALLS edge does not match verified routine endpoints."""


def verified_user_routine_call_pairs(
    code_nodes: list[CodeNode],
    calls: list[Calls],
) -> list[tuple[str, str]]:
    """Validate and deduplicate internal user-routine edge endpoints."""
    routine_ids = {
        node.id for node in code_nodes if node.label in ROUTINE_LABELS
    }
    pairs: set[tuple[str, str]] = set()
    for edge in calls:
        pair = edge.caller_id, edge.callee_id
        if edge.scope != "internal" or any(value not in routine_ids for value in pair):
            raise VerifiedCallEdgeError(
                f"CALLS endpoint is not a verified user routine: {pair!r}"
            )
        if edge.recursive != (pair[0] == pair[1]):
            raise VerifiedCallEdgeError(
                f"CALLS recursion flag differs from endpoints: {pair!r}"
            )
        pairs.add(pair)
    return sorted(pairs)
