"""Project direct, resolved routine-to-table access into product relationships.

The linker owns the endpoints, access direction and concrete data operation.
Source coordinates remain in the run ledger and are deliberately not copied to
Neo4j. Access-specific column IDs and business meaning are added later by the
function-meaning stage because the current table-insight response combines
READ and WRITE for the same table and therefore cannot safely own those fields.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from graph.product_write_batch import (
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.languages import ROUTINE_LABELS


class DataAccessLinksProjectionError(ValueError):
    """Direct data-access facts cannot be published without guessing or loss."""


DATA_ACCESS_LINK_PROPERTY_KEYS = frozenset({
    "analysis_scope_id",
    "generation_id",
    "data_operations",
})


def _unique_ids(items: Iterable[object], *, kind: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        stable_id = str(getattr(item, "id", "") or "").strip()
        if not stable_id or stable_id in result:
            raise DataAccessLinksProjectionError(
                f"{kind} IDs must be unique and non-blank: {stable_id!r}"
            )
        result[stable_id] = item
    return result


def build_data_access_links_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    code_nodes: Iterable[CodeNode],
    tables: Iterable[Table],
    reads_writes_edges: Iterable[ReadsWrites],
) -> ProductWriteBatch:
    """Build one READS/WRITES product relationship per direct routine/table pair.

    The input must already be the resolved physical-table population. An edge
    to an unresolved object reference or a non-routine CodeNode is rejected;
    silently filtering either would hide an incomplete producer contract.
    Multiple source occurrences are retained upstream but deduplicate to one
    product relationship while all verified DML operation kinds are preserved.
    """
    code_by_id = _unique_ids(code_nodes, kind="CodeNode")
    table_by_id = _unique_ids(tables, kind="Table")
    routines = {
        stable_id: node
        for stable_id, node in code_by_id.items()
        if getattr(node, "label", "") in ROUTINE_LABELS
    }

    operations_by_relation: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for edge in reads_writes_edges:
        relation = str(edge.relation or "").strip().upper()
        if relation not in {"READS", "WRITES"}:
            raise DataAccessLinksProjectionError(
                f"invalid data-access relation: {relation!r}"
            )
        source_id = str(edge.source_id or "").strip()
        target_id = str(edge.target_id or "").strip()
        if source_id not in routines:
            raise DataAccessLinksProjectionError(
                "data-access source is not a verified user routine: "
                f"{source_id!r}"
            )
        if target_id not in table_by_id:
            raise DataAccessLinksProjectionError(
                "data-access target is not a resolved physical Table: "
                f"{target_id!r}"
            )
        data_operation = str(edge.dml_type or "").strip().lower()
        relation_key = relation, source_id, target_id
        if data_operation:
            operations_by_relation[relation_key].add(data_operation)
        else:
            operations_by_relation[relation_key]

    control = {
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
    }
    relationship_operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for (relation, source_id, target_id), data_operations in sorted(
        operations_by_relation.items(),
    ):
        identity = RelationshipIdentity(
            relation,
            NodeIdentity("CodeNode", source_id),
            NodeIdentity("Table", target_id),
        )
        properties = {
            **control,
            "data_operations": tuple(sorted(data_operations)),
        }
        relationship_operations.append(
            RelationshipCreateOperation.from_properties(identity, properties)
        )
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=DATA_ACCESS_LINK_PROPERTY_KEYS,
            present_properties=properties,
        ))

    return ProductWriteBatch.build(
        stage_name="data_access_links",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        relationship_create_operations=relationship_operations,
        expectations=expectations,
    )
