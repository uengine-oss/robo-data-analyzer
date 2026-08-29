"""DBMS data-access linking uses Parser occurrences and Analyzer binding only."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from graph.schema.graph_nodes import CodeNode, Table
from pipeline.models.data_object_usage import (
    DatabaseLinkComponentEvidence,
    DataObjectUsageEvidence,
    QualifiedColumnUsage,
)
from pipeline.steps.step5_linking.link_tables.link_dbms_tables_step import (
    DbmsDataAccessContractError,
    _link,
    link_dbms_tables_step,
)


_SOURCE = "procedures/load_orders.sql"


def _routine(node_id: str = "routine:load_orders") -> CodeNode:
    return CodeNode(
        id=node_id,
        signature="load_orders()",
        file_path=_SOURCE,
        start_line=1,
        end_line=50,
        label="PROCEDURE",
        name="load_orders",
    )


def _statement(
    node_id: str,
    *,
    label: str,
    line: int,
    parent_id: str = "routine:load_orders",
) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=label,
        file_path=_SOURCE,
        start_line=line,
        end_line=line,
        label=label,
        name=label,
        parent_id=parent_id,
    )


def _usage(
    node: CodeNode,
    *,
    raw_reference: str,
    name: str,
    access: str,
    line: int | None = None,
    **kwargs,
) -> DataObjectUsageEvidence:
    return DataObjectUsageEvidence(
        source_id=node.id,
        raw_reference=raw_reference,
        name=name,
        access=access,
        evidence_file=_SOURCE,
        evidence_line=line or node.start_line,
        **kwargs,
    )


def _with_usages(
    node: CodeNode,
    *usages: DataObjectUsageEvidence,
    version: int = 4,
) -> CodeNode:
    node.extra["data_object_evidence_version"] = version
    node.extra["data_object_usages"] = tuple(usages)
    return node


class LinkDbmsTablesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.routine = _routine()
        self.tables = [
            Table(db="shop", schema="PUBLIC", name="ORDERS"),
            Table(db="shop", schema="PUBLIC", name="ORDER_ITEM"),
        ]

    def test_parser_occurrences_become_routine_to_table_products(self) -> None:
        insert = _statement("statement:insert", label="INSERT", line=10)
        _with_usages(
            insert,
            _usage(
                insert,
                raw_reference="PUBLIC.ORDER_ITEM",
                schema="PUBLIC",
                name="ORDER_ITEM",
                alias="I",
                access="WRITE",
            ),
            _usage(
                insert,
                raw_reference="PUBLIC.ORDERS",
                schema="PUBLIC",
                name="ORDERS",
                alias="O",
                access="READ",
                qualified_columns=(
                    QualifiedColumnUsage(
                        raw_reference="O.ORDER_ID",
                        qualifier="O",
                        name="ORDER_ID",
                        evidence_line=10,
                    ),
                ),
            ),
        )

        edges = _link([self.routine, insert], self.tables, datasource="shop")

        self.assertCountEqual(
            [
                (self.routine.id, self.tables[1].id, "WRITES", "INSERT"),
                (self.routine.id, self.tables[0].id, "READS", "SELECT"),
            ],
            [
                (edge.source_id, edge.target_id, edge.relation, edge.dml_type)
                for edge in edges
            ],
        )
        usages = insert.extra["data_object_usages"]
        self.assertEqual(["exact", "exact"], [usage.resolution for usage in usages])
        self.assertEqual(["shop", "shop"], [usage.datasource for usage in usages])
        self.assertEqual("ORDER_ID", usages[1].qualified_columns[0].name)

    def test_repeated_occurrences_remain_two_but_product_operation_is_one(self) -> None:
        first = _statement("statement:update:1", label="UPDATE", line=11)
        second = _statement("statement:update:2", label="UPDATE", line=21)
        for node in (first, second):
            _with_usages(
                node,
                _usage(
                    node,
                    raw_reference="PUBLIC.ORDERS",
                    schema="PUBLIC",
                    name="ORDERS",
                    access="WRITE",
                ),
            )

        edges = _link(
            [self.routine, first, second],
            self.tables,
            datasource="shop",
        )

        self.assertEqual(1, len(edges))
        self.assertEqual("UPDATE", edges[0].dml_type)
        self.assertEqual(
            [11, 21],
            [
                first.extra["data_object_usages"][0].evidence_line,
                second.extra["data_object_usages"][0].evidence_line,
            ],
        )

    def test_distinct_write_operations_are_not_collapsed_before_product_plan(self) -> None:
        insert = _statement("statement:insert", label="INSERT", line=10)
        update = _statement("statement:update", label="UPDATE", line=20)
        for node in (insert, update):
            _with_usages(
                node,
                _usage(
                    node,
                    raw_reference="PUBLIC.ORDERS",
                    schema="PUBLIC",
                    name="ORDERS",
                    access="WRITE",
                ),
            )

        edges = _link([self.routine, insert, update], self.tables)

        self.assertEqual(["INSERT", "UPDATE"], sorted(edge.dml_type for edge in edges))

    def test_unresolved_database_link_stays_in_occurrence_ledger_only(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        usage = _usage(
            select,
            raw_reference="ORDERS@REMOTE",
            name="ORDERS",
            database_link="REMOTE",
            database_link_components=(
                DatabaseLinkComponentEvidence(role="database", name="REMOTE"),
            ),
            access="READ",
        )
        _with_usages(select, usage, version=3)

        edges = _link([self.routine, select], self.tables, datasource="shop")

        self.assertEqual([], edges)
        self.assertEqual((usage.raw_reference,), tuple(
            item.raw_reference
            for item in select.extra["unresolved_data_object_usages"]
        ))
        self.assertEqual(
            ("database", "REMOTE"),
            (
                select.extra["unresolved_data_object_usages"][0]
                .database_link_components[0].role,
                select.extra["unresolved_data_object_usages"][0]
                .database_link_components[0].name,
            ),
        )

    def test_quoted_unresolved_siblings_remain_distinct_occurrences(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        usages = tuple(
            _usage(
                select,
                raw_reference=f'"App"."{name}"',
                schema="App",
                schema_quoted=True,
                name=name,
                name_quoted=True,
                access="READ",
                line=line,
            )
            for name, line in (("Foo", 12), ("foo", 13))
        )
        _with_usages(select, *usages)

        edges = _link([self.routine, select], [], datasource="shop")

        self.assertEqual([], edges)
        unresolved = select.extra["unresolved_data_object_usages"]
        self.assertEqual(
            [
                ('"App"."Foo"', "Foo", True),
                ('"App"."foo"', "foo", True),
            ],
            [
                (item.raw_reference, item.name, item.name_quoted)
                for item in unresolved
            ],
        )

    def test_quoted_and_catalog_identity_require_exact_match(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        _with_usages(
            select,
            _usage(
                select,
                raw_reference='"warehouse"."App"."Orders"',
                catalog="warehouse",
                catalog_quoted=True,
                schema="App",
                schema_quoted=True,
                name="Orders",
                name_quoted=True,
                access="READ",
            ),
        )
        tables = [
            Table(
                db="shop",
                catalog="Warehouse",
                catalog_quoted=True,
                schema="App",
                schema_quoted=True,
                name="Orders",
                name_quoted=True,
            ),
            Table(
                db="shop",
                catalog="warehouse",
                catalog_quoted=True,
                schema="App",
                schema_quoted=True,
                name="Orders",
                name_quoted=True,
            ),
        ]

        edges = _link([self.routine, select], tables)

        self.assertEqual([(self.routine.id, tables[1].id)], [
            (edge.source_id, edge.target_id) for edge in edges
        ])

    def test_ambiguous_bare_name_is_not_guessed(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        _with_usages(
            select,
            _usage(
                select,
                raw_reference="ORDERS",
                name="ORDERS",
                access="READ",
            ),
        )
        tables = [
            Table(db="shop", schema="PUBLIC", name="ORDERS"),
            Table(db="shop", schema="TENANT", name="ORDERS"),
        ]

        self.assertEqual([], _link([self.routine, select], tables))
        self.assertEqual(
            "ORDERS",
            select.extra["unresolved_data_object_usages"][0].raw_reference,
        )

    def test_complete_empty_parser_evidence_never_falls_back_to_source_scan(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        _with_usages(select, version=4)
        select.code_text = "SELECT * FROM ORDERS"

        self.assertEqual([], _link([self.routine, select], self.tables))
        self.assertEqual((), select.extra["data_object_usages"])
        self.assertEqual((), select.extra["unresolved_data_object_usages"])

    def test_dml_node_without_parser_evidence_fails_closed(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)

        with self.assertRaisesRegex(
            DbmsDataAccessContractError,
            "missing Parser data-object evidence",
        ):
            _link([self.routine, select], self.tables)

    def test_occurrence_without_owning_routine_fails_closed(self) -> None:
        select = _statement(
            "statement:select",
            label="SELECT",
            line=12,
            parent_id="",
        )
        _with_usages(
            select,
            _usage(
                select,
                raw_reference="ORDERS",
                name="ORDERS",
                access="READ",
            ),
        )

        with self.assertRaisesRegex(
            DbmsDataAccessContractError,
            "no owning user routine",
        ):
            _link([select], self.tables)

    def test_exact_resolution_outside_table_population_fails_closed(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        exact = _usage(
            select,
            raw_reference="PUBLIC.ORDERS",
            schema="PUBLIC",
            name="ORDERS",
            access="READ",
        ).resolved("db:missing")
        _with_usages(select, exact)

        with self.assertRaisesRegex(
            DbmsDataAccessContractError,
            "outside the current Table population",
        ):
            _link([self.routine, select], self.tables)

    def test_occurrence_source_mismatch_fails_closed(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        wrong = DataObjectUsageEvidence(
            source_id="another-node",
            raw_reference="ORDERS",
            name="ORDERS",
            access="READ",
            evidence_file=_SOURCE,
            evidence_line=12,
        )
        _with_usages(select, wrong)

        with self.assertRaisesRegex(
            DbmsDataAccessContractError,
            "source differs",
        ):
            _link([self.routine, select], self.tables)

    def test_step_returns_candidates_without_object_reference_state(self) -> None:
        select = _statement("statement:select", label="SELECT", line=12)
        _with_usages(
            select,
            _usage(
                select,
                raw_reference="PUBLIC.ORDERS",
                schema="PUBLIC",
                name="ORDERS",
                access="READ",
            ),
        )

        result = self._run_step([self.routine, select])

        self.assertEqual(1, len(result["reads_writes_edges"]))
        self.assertNotIn("object_references", result)

    def _run_step(self, nodes: list[CodeNode]) -> dict:
        import asyncio

        return asyncio.run(
            link_dbms_tables_step(
                {"code_nodes": nodes, "tables": self.tables},
                {"configurable": {
                    "codebase": SimpleNamespace(datasource="shop"),
                }},
            )
        )


if __name__ == "__main__":
    unittest.main()
