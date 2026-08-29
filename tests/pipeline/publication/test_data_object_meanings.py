from __future__ import annotations

import unittest

from graph.schema.graph_nodes import Column, Table
from pipeline.publication.data_object_meanings import (
    DataObjectMeaningsProjectionError,
    build_data_object_meanings_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


class DataObjectMeaningsProjectionTest(unittest.TestCase):
    def test_sufficient_and_missing_usage_context_have_exact_presence(self) -> None:
        table = Table(
            db="skt",
            schema="public",
            name="ORDERS",
            logical_name="주문",
            analyzed_description="고객 주문을 저장한다.",
            meaning_status="sufficient",
        )
        column = Column(
            id=f"{table.id}.ORDER_ID",
            name="ORDER_ID",
            dtype="VARCHAR(20)",
            meaning_status="insufficient",
            missing_context_count=1,
            missing_context_artifact_hash="d" * 64,
        )

        batch = build_data_object_meanings_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            tables=[table],
            columns=[column],
        )

        properties = {
            item.identity.canonical_key(): item.property_map
            for item in batch.expectations
        }
        table_properties = properties[f"node\x1fTable\x1f{table.id}"]
        column_properties = properties[f"node\x1fColumn\x1f{column.id}"]
        self.assertEqual("sufficient", table_properties["meaning_status"])
        self.assertNotIn("missing_context_artifact_hash", table_properties)
        self.assertEqual("insufficient", column_properties["meaning_status"])
        self.assertNotIn("logical_name", column_properties)
        self.assertNotIn("analyzed_description", column_properties)

    def test_missing_or_invalid_status_fails_before_write(self) -> None:
        table = Table(db="skt", schema="public", name="ORDERS")
        with self.assertRaisesRegex(
            DataObjectMeaningsProjectionError, "status is invalid",
        ):
            build_data_object_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                tables=[table],
                columns=[],
            )

    def test_duplicate_table_identity_fails_closed(self) -> None:
        table = Table(
            db="skt",
            schema="public",
            name="ORDERS",
            logical_name="주문",
            analyzed_description="주문을 저장한다.",
            meaning_status="sufficient",
        )
        with self.assertRaisesRegex(DataObjectMeaningsProjectionError, "unique"):
            build_data_object_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                tables=[table, table],
                columns=[],
            )


if __name__ == "__main__":
    unittest.main()
