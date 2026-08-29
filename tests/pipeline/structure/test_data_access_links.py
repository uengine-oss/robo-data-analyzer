from __future__ import annotations

import unittest

from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.structure.data_access_links import (
    DataAccessLinksProjectionError,
    build_data_access_links_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


def _routine(stable_id: str = "code:orders.load") -> CodeNode:
    return CodeNode(
        id=stable_id,
        signature="load_orders(void)",
        name="load_orders",
        label="FUNCTION",
        file_path="orders.c",
        start_line=10,
        end_line=40,
        owner_id="module:orders.c",
    )


def _table(name: str = "ORDERS") -> Table:
    return Table(db="skt", schema="public", name=name)


class DataAccessLinksProjectionTest(unittest.TestCase):
    def test_repeated_occurrences_become_one_relation_with_all_operations(self) -> None:
        routine = _routine()
        table = _table()
        batch = build_data_access_links_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[routine],
            tables=[table],
            reads_writes_edges=[
                ReadsWrites(routine.id, table.id, "WRITES", "UPDATE", "orders.c", 21),
                ReadsWrites(routine.id, table.id, "WRITES", "INSERT", "orders.c", 29),
                ReadsWrites(routine.id, table.id, "WRITES", "UPDATE", "orders.c", 35),
            ],
        )

        self.assertEqual(1, len(batch.relationship_create_operations))
        operation = batch.relationship_create_operations[0]
        self.assertEqual("WRITES", operation.identity.relationship_type)
        self.assertEqual(
            ("insert", "update"),
            dict(operation.present_properties)["data_operations"],
        )
        self.assertNotIn("evidence_file", dict(operation.present_properties))
        self.assertNotIn("evidence_line", dict(operation.present_properties))

    def test_reads_and_writes_are_distinct_product_relationships(self) -> None:
        routine = _routine()
        table = _table()
        batch = build_data_access_links_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[routine],
            tables=[table],
            reads_writes_edges=[
                ReadsWrites(routine.id, table.id, "READS", "SELECT", "orders.c", 12),
                ReadsWrites(routine.id, table.id, "WRITES", "UPDATE", "orders.c", 21),
            ],
        )

        self.assertEqual(
            ["READS", "WRITES"],
            [
                item.identity.relationship_type
                for item in batch.relationship_create_operations
            ],
        )

    def test_blank_dml_is_preserved_as_unknown_not_guessed_from_relation(self) -> None:
        routine = _routine()
        table = _table()
        batch = build_data_access_links_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[routine],
            tables=[table],
            reads_writes_edges=[
                ReadsWrites(routine.id, table.id, "READS", "", "orders.c", 12),
            ],
        )

        properties = dict(batch.relationship_create_operations[0].present_properties)
        self.assertEqual((), properties["data_operations"])

    def test_unresolved_or_unknown_table_target_fails_closed(self) -> None:
        routine = _routine()
        with self.assertRaisesRegex(
            DataAccessLinksProjectionError, "resolved physical Table",
        ):
            build_data_access_links_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[routine],
                tables=[_table()],
                reads_writes_edges=[
                    ReadsWrites(
                        routine.id,
                        "db:skt.public.MISSING_TABLE",
                        "READS",
                        "SELECT",
                        "orders.c",
                        12,
                    ),
                ],
            )

    def test_statement_source_is_not_silently_promoted_to_function(self) -> None:
        statement = CodeNode(
            id="code:orders.load:select",
            signature="",
            name="SELECT",
            label="SELECT",
            file_path="orders.sql",
            start_line=12,
            end_line=15,
            owner_id="module:orders.sql",
        )
        table = _table()
        with self.assertRaisesRegex(
            DataAccessLinksProjectionError, "verified user routine",
        ):
            build_data_access_links_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[statement],
                tables=[table],
                reads_writes_edges=[
                    ReadsWrites(statement.id, table.id, "READS", "SELECT"),
                ],
            )

    def test_duplicate_input_id_fails_before_product_mutation(self) -> None:
        routine = _routine()
        with self.assertRaisesRegex(DataAccessLinksProjectionError, "unique"):
            build_data_access_links_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[routine, routine],
                tables=[_table()],
                reads_writes_edges=[],
            )


if __name__ == "__main__":
    unittest.main()
