from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.contracts import (
    DdlTransform,
    OpMapping,
    TableAccessPattern,
    WrapperKeyExclusionRule,
    WrapperKeyRule,
)
from agents.rule_compiler import compile_exclusion_rules, compile_rule
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    TableAccessExclusionConflictError,
    RegexTableLinker,
    verify_table_patterns,
)


def _normal_pattern() -> TableAccessPattern:
    return compile_rule(WrapperKeyRule(
        id="generic_table_key",
        wrapper="db_exec",
        separator="_",
        op_tokens=["r", "w"],
        suffix_digits=3,
        entity_not_ending="_seq",
        op_mappings=[
            OpMapping(token="r", op="SELECT"),
            OpMapping(token="w", op="UPDATE"),
        ],
    ))


def _exclusions():
    return compile_exclusion_rules([WrapperKeyExclusionRule(
        id="sequence_object_key",
        wrapper="db_exec",
        entity_suffix="_seq",
        separator="_",
        op_tokens=["r", "w"],
        suffix_digits=3,
    )])


class TableAccessExclusionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "sample.c"
        self.node = CodeNode(
            id="code:f", signature="f", name="f", file_path=str(self.source),
            start_line=1, end_line=8, label="FUNCTION",
        )
        self.transform = DdlTransform(ddl_naming_rule="as_is")

    async def test_non_table_site_creates_neither_edge_nor_residual(self):
        self.source.write_text(
            'void f(void) {\n'
            '  db_exec("orders_r001");\n'
            '  db_exec("ticket_seq_r001");\n'
            '  db_exec("missing_r002");\n'
            '}\n',
            encoding="utf-8",
        )
        outcome = await RegexTableLinker().link(
            project_root=self.root,
            code_nodes=[self.node],
            tables=[
                Table(db="d", schema="s", name="orders"),
                Table(db="d", schema="s", name="ticket_seq"),
            ],
            patterns=[_normal_pattern()],
            exclusion_patterns=_exclusions(),
            ddl_transform=self.transform,
        )

        self.assertEqual(
            [Table(db="d", schema="s", name="orders").id],
            [edge.target_id for edge in outcome.edges],
        )
        self.assertEqual(["missing"], [case["attempted_patterns"][0]["groups"]["entity"] for case in outcome.unresolved])
        self.assertEqual(1, outcome.exclusion_stats["sequence_object_key"].matched_sites)

    async def test_residual_raw_pattern_overlapping_exclusion_is_rejected_even_if_ddl_exists(self):
        self.source.write_text(
            'void f(void) { db_exec("ticket_seq_r001"); }\n',
            encoding="utf-8",
        )
        broad = TableAccessPattern(
            id="raw_escape_hatch",
            call_regex=(
                r'db_exec\s*\(\s*"(?P<entity>[A-Za-z0-9_]+?)_'
                r'(?P<op_token>[rw])\d{3}"'
            ),
            op_mappings=[
                OpMapping(token="r", op="SELECT"),
                OpMapping(token="w", op="UPDATE"),
            ],
        )
        verdict = await verify_table_patterns(
            patterns=[broad],
            exclusion_patterns=_exclusions(),
            ddl_transform=self.transform,
            project_root=self.root,
            code_nodes=[self.node],
            tables=[Table(db="d", schema="s", name="ticket_seq")],
        )

        self.assertFalse(verdict.ok)
        self.assertIn("raw_escape_hatch", verdict.summary)
        self.assertIn("sequence_object_key", verdict.summary)

        with self.assertRaises(TableAccessExclusionConflictError):
            await RegexTableLinker().link(
                project_root=self.root,
                code_nodes=[self.node],
                tables=[Table(db="d", schema="s", name="ticket_seq")],
                patterns=[broad],
                exclusion_patterns=_exclusions(),
                ddl_transform=self.transform,
            )

    async def test_dead_exclusion_rule_is_rejected(self):
        self.source.write_text(
            'void f(void) { db_exec("orders_r001"); }\n',
            encoding="utf-8",
        )
        verdict = await verify_table_patterns(
            patterns=[_normal_pattern()],
            exclusion_patterns=_exclusions(),
            ddl_transform=self.transform,
            project_root=self.root,
            code_nodes=[self.node],
            tables=[Table(db="d", schema="s", name="orders")],
        )

        self.assertFalse(verdict.ok)
        self.assertIn("미관찰 제외규칙 [sequence_object_key]", verdict.summary)

    async def test_registered_contract_allows_unobserved_exclusion_in_partial_upload(self):
        self.source.write_text(
            'void f(void) { db_exec("orders_r001"); }\n',
            encoding="utf-8",
        )
        verdict = await verify_table_patterns(
            patterns=[_normal_pattern()],
            exclusion_patterns=_exclusions(),
            ddl_transform=self.transform,
            project_root=self.root,
            code_nodes=[self.node],
            tables=[],
            require_each_access_rule_match=False,
            require_each_exclusion_rule_match=False,
            require_local_table_resolution=False,
        )

        self.assertTrue(verdict.ok)
        self.assertIn("DDL미해소 100%", verdict.summary)


if __name__ == "__main__":
    unittest.main()
