"""Persist only proven internal user-routine CALLS relationships.

External, standard-library, framework-provided, ambiguous, dynamic, inactive,
and unsupported occurrences belong to the run resolution ledger. They never
create product stub nodes or CALLS relationships.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import Calls
from graph.schema.rel_types import CALLS


class CallRelationshipInvariantError(ValueError):
    """A product CALLS relationship is not a proven internal call."""


def internal_call_items(edges: Iterable[Calls]) -> list[dict]:
    """Validate and project the closed product CALLS population."""
    items: list[dict] = []
    for edge in edges:
        if edge.scope != "internal":
            raise CallRelationshipInvariantError(
                "product CALLS accepts only proven internal calls: "
                f"scope={edge.scope!r}"
            )
        endpoints_are_identical = edge.caller_id == edge.callee_id
        if endpoints_are_identical != edge.recursive:
            raise CallRelationshipInvariantError(
                "internal CALLS recursion intent does not match its endpoints: "
                f"caller={edge.caller_id!r} callee={edge.callee_id!r} "
                f"recursive={edge.recursive!r}"
            )
        items.append({
            "caller_id": edge.caller_id,
            "callee_id": edge.callee_id,
            "recursive": edge.recursive,
        })
    return items


async def emit_calls(stream, edges: Iterable[Calls]) -> None:
    """Emit the same internal-only product graph used by Neo4j storage."""
    if stream is None:
        return
    for item in internal_call_items(edges):
        await stream.emit_async({
            "type": "relationship_event",
            "op": "merge",
            "rel_type": CALLS,
            "from": {"id": item["caller_id"]},
            "to": {"id": item["callee_id"]},
            "props": {},
        })
