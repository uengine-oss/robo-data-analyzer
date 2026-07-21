from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode, Table
from agents.contracts import DdlTransform, OpMapping, TableAccessPattern
from pipeline.steps.step5_linking.link_tables.regex_table_linker import RegexTableLinker


class CAdjacentSqlLiteralTests(unittest.TestCase):
    def _link(self, source: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.c"
            path.write_text(source, encoding="utf-8")
            node = CodeNode(
                id="code:sample.c:load",
                signature="void load(void)",
                file_path=str(path),
                start_line=1,
                end_line=len(source.splitlines()),
                label="FUNCTION",
                name="load",
            )
            patterns = [
                TableAccessPattern(
                    id="raw_select",
                    call_regex=(
                        r'"(?P<op_token>SELECT)\s+.*?\bFROM\s+'
                        r'(?P<entity>[a-z_][a-z0-9_]*)(?:\s|\"|;|$)'
                    ),
                    op_mappings=[OpMapping(token="SELECT", op="SELECT")],
                ),
                TableAccessPattern(
                    id="raw_update",
                    call_regex=(
                        r'"(?P<op_token>UPDATE)\s+'
                        r'(?P<entity>[a-z_][a-z0-9_]*)(?:\s|\"|;|$)'
                    ),
                    op_mappings=[OpMapping(token="UPDATE", op="UPDATE")],
                ),
            ]
            return asyncio.run(
                RegexTableLinker().link(
                    project_root=Path(directory),
                    code_nodes=[node],
                    tables=[
                        Table(db="sample", schema="", name=name)
                        for name in ("orders", "product_stock", "order_item")
                    ],
                    patterns=patterns,
                    ddl_transform=DdlTransform(ddl_naming_rule="as_is"),
                )
            )

    def test_split_adjacent_literals_form_one_read_with_table_line_evidence(self) -> None:
        outcome = self._link(
            'void load(void) {\n'
            '  db_query("SELECT order_id "\n'
            '           "FROM orders "\n'
            '           "WHERE status = 1");\n'
            '}\n'
        )
        self.assertEqual(1, len(outcome.edges))
        edge = outcome.edges[0]
        self.assertEqual("READS", edge.relation)
        self.assertEqual("db:sample.orders", edge.target_id)
        self.assertEqual(3, edge.evidence_line)

    def test_non_adjacent_literals_are_not_synthetically_joined(self) -> None:
        outcome = self._link(
            'void load(void) {\n'
            '  const char *a = "SELECT order_id ";\n'
            '  const char *b = "FROM orders ";\n'
            '}\n'
        )
        self.assertEqual([], outcome.edges)

    def test_nested_select_in_update_records_write_and_read(self) -> None:
        outcome = self._link(
            'void load(void) {\n'
            '  db_exec("UPDATE product_stock SET hold_qty = ( "\n'
            '          "SELECT SUM(qty) FROM order_item "\n'
            '          "WHERE order_id = 1 )");\n'
            '}\n'
        )
        self.assertEqual(
            [("READS", "db:sample.order_item", 3),
             ("WRITES", "db:sample.product_stock", 2)],
            sorted((edge.relation, edge.target_id, edge.evidence_line)
            for edge in outcome.edges),
        )

    def test_delete_from_is_not_double_counted_as_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cart.c"
            source = (
                'int cart_clear(void) {\n'
                '  db_exec("DELETE FROM cart "\n'
                '          "WHERE member_id = 1");\n'
                '}\n'
            )
            path.write_text(source, encoding="utf-8")
            node = CodeNode(
                id="code:cart.c:cart_clear", signature="int cart_clear(void)",
                file_path=str(path), start_line=1, end_line=4,
                label="FUNCTION", name="cart_clear",
            )
            patterns = [
                TableAccessPattern(
                    id="delete",
                    call_regex=r"(?P<op_token>DELETE)\s+FROM\s+(?P<entity>\w+)",
                    op_mappings=[OpMapping(token="DELETE", op="DELETE")],
                ),
                TableAccessPattern(
                    id="generic_from",
                    call_regex=r"(?P<op_token>FROM)\s+(?P<entity>\w+)",
                    op_mappings=[OpMapping(token="FROM", op="SELECT")],
                ),
            ]
            outcome = asyncio.run(RegexTableLinker().link(
                project_root=Path(directory), code_nodes=[node],
                tables=[Table(db="sample", schema="", name="cart")],
                patterns=patterns,
                ddl_transform=DdlTransform(ddl_naming_rule="as_is"),
            ))

        self.assertEqual(
            [("WRITES", "db:sample.cart")],
            [(edge.relation, edge.target_id) for edge in outcome.edges],
        )

    def test_update_alias_is_a_write_and_nested_from_is_a_read(self) -> None:
        outcome = self._link(
            'void load(void) {\n'
            '  db_exec("UPDATE product_stock ps "\n'
            '          "SET ps.hold_qty = (SELECT SUM(qty) FROM order_item) "\n'
            '          "WHERE ps.id = 1");\n'
            '}\n'
        )
        self.assertEqual(
            [("READS", "db:sample.order_item"),
             ("WRITES", "db:sample.product_stock")],
            sorted((edge.relation, edge.target_id) for edge in outcome.edges),
        )


if __name__ == "__main__":
    unittest.main()
