from __future__ import annotations

import unittest

from graph.schema.graph_edges import Fk, HasColumn
from graph.schema.graph_nodes import Column, Table
from pipeline.structure.data_schema_structure import (
    COLUMN_STRUCTURE_PROPERTY_KEYS,
    TABLE_STRUCTURE_PROPERTY_KEYS,
    DataSchemaStructureProjectionError,
    build_data_schema_structure_write_batch,
)


class DataSchemaStructureProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.customer = Table(
            db="skt",
            schema="PUBLIC",
            name="CUSTOMER",
            description="customer master",
            description_source="ddl",
        )
        self.order = Table(
            db="skt",
            schema="PUBLIC",
            name="ORDERS",
        )
        self.customer_id = Column(
            id=f"{self.customer.id}.CUSTOMER_ID",
            name="CUSTOMER_ID",
            dtype="VARCHAR(20)",
            nullable=False,
            is_primary_key=True,
            is_unique=True,
        )
        self.order_customer_id = Column(
            id=f"{self.order.id}.CUSTOMER_ID",
            name="CUSTOMER_ID",
            dtype="VARCHAR(20)",
            nullable=False,
        )

    def _build(self, **overrides):
        values = {
            "analysis_scope_id": "scope",
            "generation_id": "generation",
            "tables": [self.customer, self.order],
            "columns": [self.customer_id, self.order_customer_id],
            "has_column_edges": [
                HasColumn(self.customer.id, self.customer_id.id),
                HasColumn(self.order.id, self.order_customer_id.id),
            ],
            "foreign_key_edges": [
                Fk(
                    from_id=self.order.id,
                    to_id=self.customer.id,
                    from_column="CUSTOMER_ID",
                    to_column="CUSTOMER_ID",
                ),
            ],
        }
        values.update(overrides)
        return build_data_schema_structure_write_batch(**values)

    def test_builds_closed_table_column_ownership_and_foreign_key_batch(self) -> None:
        batch = self._build()

        self.assertEqual("data_schema_structure", batch.stage_name)
        self.assertEqual(4, len(batch.node_create_operations))
        self.assertEqual(3, len(batch.relationship_create_operations))
        self.assertEqual(7, len(batch.expectations))
        relationship_types = [
            item.identity.relationship_type
            for item in batch.relationship_create_operations
        ]
        self.assertEqual(["FK", "HAS_COLUMN", "HAS_COLUMN"], relationship_types)

        fk = next(
            item for item in batch.relationship_create_operations
            if item.identity.relationship_type == "FK"
        )
        self.assertEqual(
            {
                "analysis_scope_id": "scope",
                "generation_id": "generation",
                "source_column_names": ("CUSTOMER_ID",),
                "target_column_names": ("CUSTOMER_ID",),
                "cardinalities": ("many_to_one",),
            },
            dict(fk.present_properties),
        )

    def test_optional_properties_are_absent_but_remain_in_closed_schema(self) -> None:
        batch = self._build(foreign_key_edges=[])
        table_expectation = next(
            item for item in batch.expectations
            if item.identity.product_type == "Table"
            and item.identity.stable_id == self.order.id
        )
        column_expectation = next(
            item for item in batch.expectations
            if item.identity.product_type == "Column"
            and item.identity.stable_id == self.order_customer_id.id
        )

        self.assertEqual(TABLE_STRUCTURE_PROPERTY_KEYS, set(table_expectation.closed_property_keys))
        self.assertNotIn("catalog_name", table_expectation.property_map)
        self.assertNotIn("source_description", table_expectation.property_map)
        self.assertEqual(COLUMN_STRUCTURE_PROPERTY_KEYS, set(column_expectation.closed_property_keys))
        self.assertNotIn("default_value", column_expectation.property_map)

    def test_ddl_only_unqualified_table_does_not_require_datasource_or_schema(self) -> None:
        table = Table(db="", schema="", name="LOCAL_TABLE")
        column = Column(
            id=f"{table.id}.ID",
            name="ID",
            dtype="DECIMAL(10)",
        )

        batch = self._build(
            tables=[table],
            columns=[column],
            has_column_edges=[HasColumn(table.id, column.id)],
            foreign_key_edges=[],
        )
        table_record = next(
            item for item in batch.node_create_operations
            if item.identity.product_type == "Table"
        )

        properties = dict(table_record.present_properties)
        self.assertNotIn("datasource_name", properties)
        self.assertNotIn("schema_name", properties)
        self.assertNotIn("schema_name_is_quoted", properties)
        self.assertEqual("LOCAL_TABLE", properties["physical_table_name"])

    def test_absent_qualified_namespace_cannot_be_marked_quoted(self) -> None:
        table = Table(
            db="",
            schema="",
            name="BROKEN",
            schema_quoted=True,
        )
        with self.assertRaisesRegex(
            DataSchemaStructureProjectionError,
            "absent schema as quoted",
        ):
            self._build(
                tables=[table],
                columns=[],
                has_column_edges=[],
                foreign_key_edges=[],
            )

    def test_multiple_explicit_foreign_key_rows_keep_parallel_deterministic_order(self) -> None:
        second_customer = Column(
            id=f"{self.customer.id}.TENANT_ID",
            name="TENANT_ID",
            dtype="VARCHAR(10)",
        )
        second_order = Column(
            id=f"{self.order.id}.TENANT_ID",
            name="TENANT_ID",
            dtype="VARCHAR(10)",
        )
        batch = self._build(
            columns=[
                self.customer_id,
                self.order_customer_id,
                second_customer,
                second_order,
            ],
            has_column_edges=[
                HasColumn(self.customer.id, self.customer_id.id),
                HasColumn(self.order.id, self.order_customer_id.id),
                HasColumn(self.customer.id, second_customer.id),
                HasColumn(self.order.id, second_order.id),
            ],
            foreign_key_edges=[
                Fk(self.order.id, self.customer.id, "TENANT_ID", "TENANT_ID"),
                Fk(self.order.id, self.customer.id, "CUSTOMER_ID", "CUSTOMER_ID"),
            ],
        )
        fk = next(
            item for item in batch.relationship_create_operations
            if item.identity.relationship_type == "FK"
        )
        properties = dict(fk.present_properties)
        self.assertEqual(
            ("CUSTOMER_ID", "TENANT_ID"),
            properties["source_column_names"],
        )
        self.assertEqual(
            ("CUSTOMER_ID", "TENANT_ID"),
            properties["target_column_names"],
        )

    def test_column_without_has_column_owner_fails(self) -> None:
        with self.assertRaisesRegex(
            DataSchemaStructureProjectionError,
            "Column has no HAS_COLUMN owner",
        ):
            self._build(has_column_edges=[])

    def test_duplicate_has_column_edge_fails(self) -> None:
        edge = HasColumn(self.customer.id, self.customer_id.id)
        with self.assertRaisesRegex(
            DataSchemaStructureProjectionError,
            "duplicate HAS_COLUMN edge",
        ):
            self._build(has_column_edges=[
                edge,
                edge,
                HasColumn(self.order.id, self.order_customer_id.id),
            ])

    def test_foreign_key_endpoint_outside_batch_fails(self) -> None:
        with self.assertRaisesRegex(
            DataSchemaStructureProjectionError,
            "FK endpoint is outside",
        ):
            self._build(foreign_key_edges=[
                Fk(self.order.id, "missing-table", "CUSTOMER_ID", "ID"),
            ])

    def test_candidate_foreign_key_is_not_promoted_to_product_graph(self) -> None:
        with self.assertRaisesRegex(
            DataSchemaStructureProjectionError,
            "candidate FK cannot be published",
        ):
            self._build(foreign_key_edges=[
                Fk(
                    self.order.id,
                    self.customer.id,
                    "CUSTOMER_ID",
                    "CUSTOMER_ID",
                    is_candidate=True,
                ),
            ])

    def test_duplicate_explicit_foreign_key_row_fails(self) -> None:
        edge = Fk(
            self.order.id,
            self.customer.id,
            "CUSTOMER_ID",
            "CUSTOMER_ID",
        )
        with self.assertRaisesRegex(
            DataSchemaStructureProjectionError,
            "duplicate explicit FK row",
        ):
            self._build(foreign_key_edges=[edge, edge])

    def test_description_requires_its_origin(self) -> None:
        table = Table(
            db="skt",
            schema="PUBLIC",
            name="BROKEN",
            description="has text but no provenance",
        )
        with self.assertRaisesRegex(
            DataSchemaStructureProjectionError,
            "description without its origin",
        ):
            self._build(tables=[table], columns=[], has_column_edges=[], foreign_key_edges=[])


if __name__ == "__main__":
    unittest.main()
