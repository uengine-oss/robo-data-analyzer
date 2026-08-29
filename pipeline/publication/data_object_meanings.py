"""Project validated Table and Column meanings into one immutable patch batch."""
from __future__ import annotations

from typing import Iterable

from graph.data_object_meaning import validated_data_object_meaning_item
from graph.product_write_batch import (
    NodeIdentity,
    NodePropertySetOperation,
    ProductRecordExpectation,
    ProductWriteBatch,
)
from graph.schema.graph_nodes import Column, Table


class DataObjectMeaningsProjectionError(ValueError):
    """A Table or Column meaning violates the closed product presence rules."""


DATA_OBJECT_MEANING_PROPERTY_KEYS = frozenset({
    "logical_name",
    "analyzed_description",
    "meaning_status",
    "missing_context_count",
    "missing_context_artifact_hash",
})


def _validated_properties(entity: object, *, entity_kind: str) -> dict[str, object]:
    item = {
        "id": str(getattr(entity, "id", "") or ""),
        "logical_name": str(getattr(entity, "logical_name", "") or ""),
        "analyzed_description": str(
            getattr(entity, "analyzed_description", "") or ""
        ),
        "meaning_status": str(getattr(entity, "meaning_status", "") or ""),
        "missing_context_count": getattr(entity, "missing_context_count", None),
        "missing_context_artifact_hash": str(
            getattr(entity, "missing_context_artifact_hash", "") or ""
        ),
    }
    try:
        validated = validated_data_object_meaning_item(
            item, entity_kind=entity_kind,
        )
    except ValueError as error:
        raise DataObjectMeaningsProjectionError(str(error)) from error
    validated.pop("id")
    return validated


def build_data_object_meanings_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    tables: Iterable[Table],
    columns: Iterable[Column],
) -> ProductWriteBatch:
    """Build the complete Table/Column meaning obligation for one generation."""
    entities = [
        ("Table", "table", table) for table in tables
    ] + [
        ("Column", "column", column) for column in columns
    ]
    seen: set[tuple[str, str]] = set()
    patches: list[NodePropertySetOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for product_type, entity_kind, entity in sorted(
        entities,
        key=lambda item: (item[0], str(getattr(item[2], "id", ""))),
    ):
        stable_id = str(getattr(entity, "id", "") or "").strip()
        identity_key = product_type, stable_id
        if not stable_id or identity_key in seen:
            raise DataObjectMeaningsProjectionError(
                f"{product_type} IDs must be unique and non-blank: {stable_id!r}"
            )
        seen.add(identity_key)
        properties = _validated_properties(entity, entity_kind=entity_kind)
        identity = NodeIdentity(product_type, stable_id)
        patches.extend(
            NodePropertySetOperation.from_value(identity, key, value)
            for key, value in sorted(properties.items())
        )
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=DATA_OBJECT_MEANING_PROPERTY_KEYS,
            present_properties=properties,
        ))

    return ProductWriteBatch.build(
        stage_name="table_meanings",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_property_set_operations=patches,
        expectations=expectations,
    )
