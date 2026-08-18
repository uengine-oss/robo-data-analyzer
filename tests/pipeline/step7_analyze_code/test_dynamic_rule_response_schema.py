"""Rule wire retirement and deterministic DBMS ownership contracts."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, ControlSubtreeNode
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    RuleItem,
    RulesExamplesOutput,
)
from pipeline.steps.step7_analyze_code.batch_analyze import _materialize_dbms_rules
from shared.files.cache import SourceFileCache


class ClosedRuleProjectionTest(unittest.TestCase):
    def test_rule_schema_contains_only_parser_owned_structure_and_empty_nl(self):
        schema = RuleItem.model_json_schema()

        self.assertEqual(
            {"anchor_line", "cond", "then", "nl"},
            set(schema["properties"]),
        )
        self.assertEqual("", schema["properties"]["nl"]["const"])
        self.assertNotIn("semantic_frame", schema["properties"])
        self.assertNotIn("business_meaning", schema["properties"])

    def test_nonempty_narrative_and_historical_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            RuleItem(
                anchor_line=1, cond='F("ready")', then=[], nl="업무 의미",
            )
        with self.assertRaises(ValidationError):
            RuleItem.model_validate({
                "anchor_line": 1,
                "cond": 'F("ready")',
                "then": [],
                "nl": "",
                "business_meaning": "invented",
            })
        with self.assertRaises(ValidationError):
            RulesExamplesOutput.model_validate({
                "rules": [], "semantic_frame": {"schema": "historical"},
            })

    def test_dbms_batch_rule_item_has_no_model_semantic_envelope(self):
        item = BatchRulesItem(
            node_key="1-3",
            rules=[RuleItem(anchor_line=1, cond='F("ready")', then=[])],
        )

        self.assertEqual("", item.rules[0].nl)
        self.assertEqual({"node_key", "rules"}, set(item.model_dump()))
        with self.assertRaises(ValidationError):
            BatchRulesItem.model_validate({
                "node_key": "1-3", "rules": [], "semantic_frame": {},
            })


class DbmsDeterministicWiringTest(unittest.TestCase):
    def test_dbms_assigned_rows_are_materialized_without_provider_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text(
                "IF flag = 1 THEN\n  act();\nEND IF;\n",
                encoding="utf-8",
            )
            routine = CodeNode(
                id="p", name="p", signature="p", file_path=str(source),
                start_line=1, end_line=3, label="PROCEDURE",
            )
            node = CodeNode(
                id="a", name="if", signature="if", file_path=str(source),
                start_line=1, end_line=3, label="IF", parent_id="p",
            )
            routine_context = AnalysisContext(
                id="p", signature="p", file_path=str(source),
                start_line=1, end_line=3,
                extra={
                    "control_subtree": [ControlSubtreeNode(
                        node_id="if", ntype="IF", start=1, end=3,
                        expression='flag == "1"',
                    )],
                    "ast_statements": (),
                    "ast_call_lines": ((2, "act"),),
                },
            )
            context = AnalysisContext(
                id="a", signature="if", file_path=str(source),
                start_line=1, end_line=3,
                extra={"rule_rows": (1,), "ast_statements": ()},
            )

            result = _materialize_dbms_rules(
                ["a"],
                contexts={"a": context, "p": routine_context},
                code_by_id={"a": node, "p": routine},
                source_cache=SourceFileCache(),
            )

        self.assertEqual({"a"}, set(result.items))
        self.assertEqual({}, result.findings)
        self.assertEqual("1-3", result.items["a"].node_key)
        self.assertEqual([1], [rule.anchor_line for rule in result.items["a"].rules])
        self.assertEqual(['call("act")'], result.items["a"].rules[0].then)
        self.assertEqual("", result.items["a"].rules[0].nl)


if __name__ == "__main__":
    unittest.main()
