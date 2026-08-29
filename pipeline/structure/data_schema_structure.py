"""Project verified DDL structure into one immutable product write batch.

DDL parsing already owns physical table/column identity, column order and
explicit foreign-key facts.  This module does not inspect SQL text and does not
infer schema facts.  It validates that closed population and translates only
the product-visible physical structure.  Raw comments and view definitions stay
in Analyzer state/run artifacts because no published consumer needs them.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.schema.graph_edges import Fk, HasColumn
from graph.schema.graph_nodes import Column, Table


class DataSchemaStructureProjectionError(ValueError):
    """Verified DDL structure cannot be published without loss or guessing."""


TABLE_STRUCTURE_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
    "datasource_name",
    "catalog_name",
    "catalog_name_is_quoted",
    "schema_name",
    "schema_name_is_quoted",
    "physical_table_name",
    "physical_table_name_is_quoted",
    "table_type",
    "source_description",
    "source_description_origin",
})

COLUMN_STRUCTURE_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
    "physical_column_name",
    "physical_column_name_is_quoted",
    "data_type",
    "is_nullable",
    "is_primary_key",
    "is_unique",
    "default_value",
    "source_description",
    "source_description_origin",
})

STRUCTURE_RELATIONSHIP_PROPERTY_KEYS = frozenset({
    "analysis_scope_id",
    "generation_id",
})

FOREIGN_KEY_PROPERTY_KEYS = frozenset({
    "analysis_scope_id",
    "generation_id",
    "source_column_names",
    "target_column_names",
    "cardinalities",
})


def _require_nonblank(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataSchemaStructureProjectionError(
            f"{name} must be a non-blank string"
        )
    return value


def _unique_by_id(
    items: Iterable[Table | Column],
    *,
    kind: str,
) -> dict[str, Table | Column]:
    indexed: dict[str, Table | Column] = {}
    for item in items:
        stable_id = _require_nonblank(f"{kind} id", item.id)
        if stable_id in indexed:
            raise DataSchemaStructureProjectionError(
                f"duplicate {kind} id: {stable_id!r}"
            )
        indexed[stable_id] = item
    return indexed


def _optional_source_description(
    *,
    owner_kind: str,
    owner_id: str,
    description: str,
    description_origin: str,
) -> dict[str, object]:
    description = str(description or "").strip()
    description_origin = str(description_origin or "").strip()
    if description_origin and not description:
        raise DataSchemaStructureProjectionError(
            f"{owner_kind} {owner_id!r} has a description origin without a description"
        )
    if not description:
        return {}
    if not description_origin:
        raise DataSchemaStructureProjectionError(
            f"{owner_kind} {owner_id!r} has a description without its origin"
        )
    return {
        "source_description": description,
        "source_description_origin": description_origin,
    }


def _node_record(
    identity: NodeIdentity,
    *,
    closed_property_keys: Iterable[str],
    present_properties: Mapping[str, object],
) -> tuple[NodeCreateOperation, ProductRecordExpectation]:
    return (
        NodeCreateOperation.from_properties(identity, present_properties),
        ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=closed_property_keys,
            present_properties=present_properties,
        ),
    )


def _relationship_record(
    identity: RelationshipIdentity,
    *,
    closed_property_keys: Iterable[str],
    present_properties: Mapping[str, object],
) -> tuple[RelationshipCreateOperation, ProductRecordExpectation]:
    return (
        RelationshipCreateOperation.from_properties(identity, present_properties),
        ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=closed_property_keys,
            present_properties=present_properties,
        ),
    )


def build_data_schema_structure_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    tables: Iterable[Table],
    columns: Iterable[Column],
    has_column_edges: Iterable[HasColumn],
    foreign_key_edges: Iterable[Fk],
) -> ProductWriteBatch:
    """Build the complete physical table/column publication obligation.

    Every Column must have exactly one HAS_COLUMN owner.  Every relationship
    endpoint must be in this batch.  Explicit foreign-key column pairs between
    the same table pair are preserved as ordered parallel arrays on one product
    relationship because product relationship identity is type + endpoints.
    Inferred/candidate foreign keys are not verified product facts and fail
    before any database write.
    """
    table_by_id = _unique_by_id(tables, kind="Table")
    column_by_id = _unique_by_id(columns, kind="Column")
    table_identity = {
        stable_id: NodeIdentity("Table", stable_id)
        for stable_id in table_by_id
    }
    column_identity = {
        stable_id: NodeIdentity("Column", stable_id)
        for stable_id in column_by_id
    }

    table_for_column: dict[str, str] = {}
    has_column_pairs: set[tuple[str, str]] = set()
    for edge in has_column_edges:
        pair = edge.parent_id, edge.child_id
        if pair in has_column_pairs:
            raise DataSchemaStructureProjectionError(
                f"duplicate HAS_COLUMN edge: {pair!r}"
            )
        has_column_pairs.add(pair)
        if edge.parent_id not in table_by_id:
            raise DataSchemaStructureProjectionError(
                f"HAS_COLUMN has unknown Table: {edge.parent_id!r}"
            )
        if edge.child_id not in column_by_id:
            raise DataSchemaStructureProjectionError(
                f"HAS_COLUMN has unknown Column: {edge.child_id!r}"
            )
        previous = table_for_column.setdefault(edge.child_id, edge.parent_id)
        if previous != edge.parent_id:
            raise DataSchemaStructureProjectionError(
                f"Column {edge.child_id!r} belongs to multiple Tables"
            )

    missing_column_owners = sorted(set(column_by_id) - set(table_for_column))
    if missing_column_owners:
        raise DataSchemaStructureProjectionError(
            f"Column has no HAS_COLUMN owner: {missing_column_owners[0]!r}"
        )

    foreign_keys_by_table_pair: dict[tuple[str, str], list[Fk]] = defaultdict(list)
    seen_foreign_key_rows: set[tuple[str, str, str, str, str]] = set()
    for edge in foreign_key_edges:
        if edge.from_id not in table_by_id or edge.to_id not in table_by_id:
            raise DataSchemaStructureProjectionError(
                "FK endpoint is outside the verified Table population: "
                f"{edge.from_id!r} -> {edge.to_id!r}"
            )
        source_column = _require_nonblank("FK source column", edge.from_column)
        target_column = _require_nonblank("FK target column", edge.to_column)
        cardinality = _require_nonblank("FK cardinality", edge.fk_type)
        if edge.is_candidate:
            raise DataSchemaStructureProjectionError(
                "candidate FK cannot be published as a verified product relationship: "
                f"{edge.from_id!r} -> {edge.to_id!r}"
            )
        row_key = (
            edge.from_id,
            edge.to_id,
            source_column,
            target_column,
            cardinality,
        )
        if row_key in seen_foreign_key_rows:
            raise DataSchemaStructureProjectionError(
                f"duplicate explicit FK row: {row_key!r}"
            )
        seen_foreign_key_rows.add(row_key)
        foreign_keys_by_table_pair[(edge.from_id, edge.to_id)].append(edge)

    control = {
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
    }
    node_operations: list[NodeCreateOperation] = []
    relationship_operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []

    for stable_id, raw_table in sorted(table_by_id.items()):
        table = raw_table
        properties: dict[str, object] = {
            **control,
            "stable_id": stable_id,
            "physical_table_name": _require_nonblank(
                f"Table {stable_id!r} physical name", table.name,
            ),
            "physical_table_name_is_quoted": table.name_quoted,
            "table_type": _require_nonblank(
                f"Table {stable_id!r} type", table.table_type,
            ),
            **_optional_source_description(
                owner_kind="Table",
                owner_id=stable_id,
                description=table.description,
                description_origin=table.description_source,
            ),
        }
        # A datasource is enrichment input, not a prerequisite for a valid DDL
        # upload.  Likewise, SQL permits an unqualified table name.  Publish
        # those namespaces only when the source actually supplied them; the
        # stable table identity and analysis scope still close the population.
        if table.db:
            properties["datasource_name"] = table.db
        if table.schema:
            properties["schema_name"] = table.schema
            properties["schema_name_is_quoted"] = table.schema_quoted
        elif table.schema_quoted:
            raise DataSchemaStructureProjectionError(
                f"Table {stable_id!r} marks an absent schema as quoted"
            )
        if table.catalog:
            properties["catalog_name"] = table.catalog
            properties["catalog_name_is_quoted"] = table.catalog_quoted
        elif table.catalog_quoted:
            raise DataSchemaStructureProjectionError(
                f"Table {stable_id!r} marks an absent catalog as quoted"
            )
        operation, expectation = _node_record(
            table_identity[stable_id],
            closed_property_keys=TABLE_STRUCTURE_PROPERTY_KEYS,
            present_properties=properties,
        )
        node_operations.append(operation)
        expectations.append(expectation)

    for stable_id, raw_column in sorted(column_by_id.items()):
        column = raw_column
        properties = {
            **control,
            "stable_id": stable_id,
            "physical_column_name": _require_nonblank(
                f"Column {stable_id!r} physical name", column.name,
            ),
            "physical_column_name_is_quoted": column.name_quoted,
            "data_type": _require_nonblank(
                f"Column {stable_id!r} data type", column.dtype,
            ),
            "is_nullable": column.nullable,
            "is_primary_key": column.is_primary_key,
            "is_unique": column.is_unique,
            **_optional_source_description(
                owner_kind="Column",
                owner_id=stable_id,
                description=column.description,
                description_origin=column.description_source,
            ),
        }
        if column.default_value is not None:
            properties["default_value"] = column.default_value
        operation, expectation = _node_record(
            column_identity[stable_id],
            closed_property_keys=COLUMN_STRUCTURE_PROPERTY_KEYS,
            present_properties=properties,
        )
        node_operations.append(operation)
        expectations.append(expectation)

    for table_id, column_id in sorted(has_column_pairs):
        identity = RelationshipIdentity(
            "HAS_COLUMN", table_identity[table_id], column_identity[column_id],
        )
        operation, expectation = _relationship_record(
            identity,
            closed_property_keys=STRUCTURE_RELATIONSHIP_PROPERTY_KEYS,
            present_properties=control,
        )
        relationship_operations.append(operation)
        expectations.append(expectation)

    for (source_table_id, target_table_id), rows in sorted(
        foreign_keys_by_table_pair.items(),
    ):
        ordered_rows = sorted(
            rows,
            key=lambda item: (
                item.from_column,
                item.to_column,
                item.fk_type,
            ),
        )
        properties = {
            **control,
            "source_column_names": tuple(item.from_column for item in ordered_rows),
            "target_column_names": tuple(item.to_column for item in ordered_rows),
            "cardinalities": tuple(item.fk_type for item in ordered_rows),
        }
        identity = RelationshipIdentity(
            "FK",
            table_identity[source_table_id],
            table_identity[target_table_id],
        )
        operation, expectation = _relationship_record(
            identity,
            closed_property_keys=FOREIGN_KEY_PROPERTY_KEYS,
            present_properties=properties,
        )
        relationship_operations.append(operation)
        expectations.append(expectation)

    return ProductWriteBatch.build(
        stage_name="data_schema_structure",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=sorted(
            node_operations, key=lambda item: item.identity.canonical_key(),
        ),
        relationship_create_operations=sorted(
            relationship_operations,
            key=lambda item: item.identity.canonical_key(),
        ),
        expectations=sorted(
            expectations, key=lambda item: item.identity.canonical_key(),
        ),
    )
