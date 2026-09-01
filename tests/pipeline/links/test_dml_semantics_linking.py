"""Framework embedded-SQL semantics and source-owner contracts.

DBMS SQL syntax is covered by Parser data-object evidence tests.  Only embedded
SQL inside framework source remains a verified Analyzer scanning path.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from graph.schema.graph_nodes import CodeNode
from pipeline.stages.links.tables.regex_table_linker import (
    CodeLocator,
    _c_sql_semantic_edges,
)


def _node(
    node_id: str,
    file_path: str,
    start: int,
    end: int,
    *,
    label: str = "PROCEDURE",
) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path=file_path,
        start_line=start,
        end_line=end,
        label=label,
        name=node_id,
    )


class EmbeddedSqlSemanticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.transform = SimpleNamespace(
            ddl_naming_rule="as_is",
            fallback_prefixes=[],
        )

    def _run_c(self, c_code: str, table_names: list[str]):
        src = self.dir / "case.c"
        src.write_text(c_code, encoding="utf-8")
        lookup = {name: f"db:d.S.{name}" for name in table_names}
        locator = CodeLocator([
            _node("f:1", str(src), 1, c_code.count("\n") + 1),
        ])
        return lookup, _c_sql_semantic_edges(
            c_code,
            src,
            locator,
            lookup,
            self.transform,
        )

    def test_merge_using_source_is_read(self) -> None:
        lookup, edges = self._run_c(
            'void f(void) {\n'
            '  exec_sql("MERGE INTO T_DST D USING T_SRC S ON (D.ID=S.ID) "\n'
            '           "WHEN MATCHED THEN UPDATE SET D.X = S.X");\n'
            '}\n',
            ["T_DST", "T_SRC"],
        )

        by_target = {edge.target_id: edge.relation for edge in edges}
        self.assertEqual("WRITES", by_target[lookup["T_DST"]])
        self.assertEqual("READS", by_target[lookup["T_SRC"]])

    def test_truncate_literal_is_write(self) -> None:
        _, edges = self._run_c(
            'void f(void) { run("TRUNCATE TABLE TD_LOG"); }\n',
            ["TD_LOG"],
        )

        self.assertEqual(
            [("WRITES", "TRUNCATE")],
            [(edge.relation, edge.dml_type) for edge in edges],
        )


class CodeLocatorOwnershipTest(unittest.TestCase):
    def test_component_suffix_does_not_confuse_similar_directory_name(self) -> None:
        locator = CodeLocator([_node("right", "a/x.c", 1, 5)])
        self.assertIsNone(locator.locate("ba/x.c", 2))

    def test_duplicate_basename_is_explicitly_ambiguous(self) -> None:
        locator = CodeLocator([
            _node("left", "left/x.c", 1, 5),
            _node("right", "right/x.c", 1, 5),
        ])
        with self.assertRaisesRegex(ValueError, "ambiguous CodeNode file ownership"):
            locator.locate("x.c", 2)

    def test_nested_code_range_uses_narrowest_owner(self) -> None:
        locator = CodeLocator([
            _node("outer", "x.c", 1, 20),
            _node("inner", "x.c", 5, 10),
        ])
        self.assertEqual("inner", locator.locate("x.c", 7))

    def test_equal_code_ranges_are_explicitly_ambiguous(self) -> None:
        locator = CodeLocator([
            _node("one", "x.c", 1, 5),
            _node("two", "x.c", 1, 5),
        ])
        with self.assertRaisesRegex(ValueError, "ambiguous CodeNode line ownership"):
            locator.locate("x.c", 2)

    def test_control_ranges_do_not_compete_with_routine_ownership(self) -> None:
        locator = CodeLocator([
            _node("routine", "x.c", 1, 20),
            _node("if", "x.c", 5, 10, label="IF"),
            _node("else", "x.c", 5, 10, label="ELSE"),
        ])
        self.assertEqual("routine", locator.locate("x.c", 7))


if __name__ == "__main__":
    unittest.main()
