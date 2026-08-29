"""Project proven internal routine calls into the product CALLS batch.

Every parser call occurrence remains in ``call_resolution_ledger``.  The
published graph intentionally contains only one relationship for each distinct
internal user-routine pair.  External, standard-library, framework-provided,
dynamic and unresolved occurrences therefore produce no product operation.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from graph.product_write_batch import (
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import ROUTINE_LABELS
from pipeline.models.call_resolution import normalize_call_resolution_ledger


class CallLinksProjectionError(ValueError):
    """Call occurrence accounting and product CALLS projection disagree."""


CALLS_PROPERTY_KEYS = frozenset({"analysis_scope_id", "generation_id"})


def _routine_index(code_nodes: Iterable[CodeNode]) -> dict[str, CodeNode]:
    routines: dict[str, CodeNode] = {}
    all_ids: set[str] = set()
    for node in code_nodes:
        if not node.id or node.id in all_ids:
            raise CallLinksProjectionError(
                f"CodeNode IDs must be unique and non-blank: {node.id!r}"
            )
        all_ids.add(node.id)
        if node.label in ROUTINE_LABELS:
            routines[node.id] = node
    return routines


def build_call_links_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    code_nodes: Iterable[CodeNode],
    calls_edges: Iterable[Calls],
    call_resolution_ledger: Sequence[Mapping[str, object]],
) -> ProductWriteBatch:
    """Build one deduplicated CALLS relationship per internal ledger pair."""
    routines = _routine_index(code_nodes)
    try:
        ledger = normalize_call_resolution_ledger(call_resolution_ledger)
    except ValueError as error:
        raise CallLinksProjectionError(
            f"call resolution ledger is not closed: {error}"
        ) from error

    expected_internal_pairs: set[tuple[str, str]] = set()
    for row in ledger:
        if row["resolution_status"] != "internal":
            if row.get("callee_id"):
                raise CallLinksProjectionError(
                    "non-internal call occurrence claims a product callee: "
                    f"{row['call_id']!r}"
                )
            continue
        pair = str(row["caller_id"]), str(row["callee_id"])
        if pair[0] not in routines or pair[1] not in routines:
            raise CallLinksProjectionError(
                "internal call endpoint is not a verified user routine: "
                f"{pair!r}"
            )
        expected_internal_pairs.add(pair)

    actual_internal_pairs: set[tuple[str, str]] = set()
    for edge in calls_edges:
        if edge.scope != "internal":
            raise CallLinksProjectionError(
                f"product call edge is not internal: {edge.scope!r}"
            )
        pair = edge.caller_id, edge.callee_id
        if pair[0] not in routines or pair[1] not in routines:
            raise CallLinksProjectionError(
                "call edge endpoint is not a verified user routine: "
                f"{pair!r}"
            )
        if edge.recursive != (pair[0] == pair[1]):
            raise CallLinksProjectionError(
                f"call edge recursion flag disagrees with endpoints: {pair!r}"
            )
        actual_internal_pairs.add(pair)

    if actual_internal_pairs != expected_internal_pairs:
        missing = sorted(expected_internal_pairs - actual_internal_pairs)
        unexpected = sorted(actual_internal_pairs - expected_internal_pairs)
        raise CallLinksProjectionError(
            "CALLS edges differ from the internal occurrence projection: "
            f"missing={missing}, unexpected={unexpected}"
        )

    control = {
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
    }
    operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for caller_id, callee_id in sorted(expected_internal_pairs):
        identity = RelationshipIdentity(
            "CALLS",
            NodeIdentity("CodeNode", caller_id),
            NodeIdentity("CodeNode", callee_id),
        )
        operations.append(RelationshipCreateOperation.from_properties(
            identity, control,
        ))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=CALLS_PROPERTY_KEYS,
            present_properties=control,
        ))

    return ProductWriteBatch.build(
        stage_name="call_links",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        relationship_create_operations=operations,
        expectations=expectations,
    )
