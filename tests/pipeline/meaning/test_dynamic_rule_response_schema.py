"""Rule wire retirement and deterministic DBMS ownership contracts."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, ControlSubtreeNode
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    RuleCollectionOutput,
    RuleItem,
)
from pipeline.meaning.batch_analyze import (
    DbmsRuleMaterialization,
    _collect_dbms_rule_meanings,
    _materialize_dbms_rules,
)
from shared.concurrency.priority_semaphore import PrioritySemaphore
from shared.files.source_cache import SourceFileCache
from tests.pipeline.parser_call_fixtures import parser_call_occurrences


class ClosedRuleProjectionTest(unittest.TestCase):
    def test_rule_schema_separates_structural_facts_from_bounded_business_meaning(self):
        schema = RuleItem.model_json_schema()

        self.assertEqual(
            {
                "anchor_line", "structural_condition", "structural_effects",
                "condition_description", "effect_descriptions",
            },
            set(schema["properties"]),
        )
        self.assertNotIn("semantic_frame", schema["properties"])
        self.assertNotIn("business_meaning", schema["properties"])

    def test_nonempty_narrative_and_historical_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            RuleItem.model_validate({
                "anchor_line": 1,
                "structural_condition": 'F("ready")',
                "structural_effects": [],
                "business_meaning": "invented",
            })
        with self.assertRaises(ValidationError):
            RuleCollectionOutput.model_validate({
                "rules": [], "semantic_frame": {"schema": "historical"},
            })

    def test_dbms_batch_rule_item_has_no_model_semantic_envelope(self):
        item = BatchRulesItem(
            node_key="1-3",
            rules=[RuleItem(
                anchor_line=1,
                structural_condition='F("ready")',
                structural_effects=[],
            )],
        )

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
                    "ast_call_occurrences": parser_call_occurrences(((2, "act"),)),
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
        self.assertEqual("N1", result.items["a"].node_key)
        self.assertEqual([1], [rule.anchor_line for rule in result.items["a"].rules])
        self.assertEqual(
            ['call("act", None, [])'], result.items["a"].rules[0].structural_effects,
        )
        self.assertEqual({"a"}, set(result.evidence))


class DbmsRuleMeaningWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_batch_call_fills_exact_atoms_without_second_pass(self) -> None:
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
                    "ast_call_occurrences": parser_call_occurrences(((2, "act"),)),
                },
            )
            context = AnalysisContext(
                id="a", signature="if", file_path=str(source),
                start_line=1, end_line=3,
                extra={"rule_rows": (1,), "ast_statements": ()},
            )
            contexts = {"a": context, "p": routine_context}
            materialized = _materialize_dbms_rules(
                ["a"],
                contexts=contexts,
                code_by_id={"a": node, "p": routine},
                source_cache=SourceFileCache(),
            )

            async def respond(*, response_model, **_kwargs):
                evidence = materialized.evidence["a"]
                return response_model.model_validate({
                    "nodes": {
                        "N1": {
                            "conditions": {
                                key: "플래그가 설정된 경우"
                                for key in evidence["conditions"]
                            },
                            "effects": {
                                key: "업무 처리를 호출한다"
                                for key in evidence["effects"]
                            },
                            "rows": {
                                "R1": "business_rule"
                            },
                        }
                    }
                })

            run = AsyncMock(side_effect=respond)
            with patch(
                "pipeline.meaning.batch_analyze.run_yaml_rule", run,
            ):
                completed = await _collect_dbms_rule_meanings(
                    materialized,
                    parent_id="p",
                    contexts=contexts,
                    strategy=SimpleNamespace(prompts_dir="."),
                    cost_budget=None,
                    table_descriptions={},
                    source_cache=SourceFileCache(),
                    llm_slots=PrioritySemaphore(1),
                )

        self.assertEqual(1, run.await_count)
        [rule] = completed.items["a"].rules
        self.assertTrue(rule.structural_condition)
        self.assertTrue(rule.condition_description)
        self.assertTrue(rule.effect_descriptions)

    async def test_partitioned_batches_cover_each_row_once_even_out_of_order(self) -> None:
        evidence = {
            "conditions": {},
            "effects": {
                "E1": {
                    "op": "ret", "target": None, "value": "FIRST",
                    "from_source": True,
                },
                "E2": {
                    "op": "ret", "target": None, "value": "SECOND",
                    "from_source": True,
                },
            },
            "rows": {
                "R1": {"role": "exit", "when": [], "direct": ["E1"]},
                "R2": {"role": "exit", "when": [], "direct": ["E2"]},
            },
        }
        partitions = (
            {
                "conditions": {},
                "effects": {"E1": evidence["effects"]["E1"]},
                "rows": {"R1": evidence["rows"]["R1"]},
            },
            {
                "conditions": {},
                "effects": {"E2": evidence["effects"]["E2"]},
                "rows": {"R2": evidence["rows"]["R2"]},
            },
        )
        materialized = DbmsRuleMaterialization(
            items={
                "a": BatchRulesItem(
                    node_key="N1",
                    rules=[
                        RuleItem(anchor_line=1, structural_effects=['ret("FIRST")']),
                        RuleItem(anchor_line=2, structural_effects=['ret("SECOND")']),
                    ],
                ),
            },
            findings={},
            evidence={"a": evidence},
        )
        context = AnalysisContext(
            id="a", signature="a", file_path="sample.sql",
            start_line=1, end_line=2,
        )
        completion_order: list[str] = []

        async def respond(*, user_values, response_model, **_kwargs):
            is_first = "FIRST" in user_values["rule_packets"]
            if is_first:
                await asyncio.sleep(0.02)
            completion_order.append("first" if is_first else "second")
            return response_model.model_validate({
                "nodes": {
                    "N1": {
                        "conditions": {},
                        "effects": {"E1": "first" if is_first else "second"},
                        "rows": {"R1": "business_rule"},
                    },
                },
            })

        with (
            patch(
                "pipeline.meaning.batch_analyze.partition_rule_meaning_evidence",
                return_value=partitions,
            ),
            patch(
                "pipeline.meaning.batch_analyze.configured_rule_effect_limit",
                return_value=1,
            ),
            patch(
                "pipeline.meaning.batch_analyze.run_yaml_rule",
                side_effect=respond,
            ) as run,
        ):
            completed = await _collect_dbms_rule_meanings(
                materialized,
                parent_id="p",
                contexts={"a": context},
                strategy=SimpleNamespace(prompts_dir="."),
                cost_budget=None,
                table_descriptions={},
                source_cache=SourceFileCache(),
                llm_slots=PrioritySemaphore(2),
            )

        self.assertEqual(2, run.await_count)
        self.assertEqual(["second", "first"], completion_order)
        self.assertEqual(
            [1, 2], [rule.anchor_line for rule in completed.items["a"].rules],
        )
        self.assertEqual(
            [["first"], ["second"]],
            [rule.effect_descriptions for rule in completed.items["a"].rules],
        )


if __name__ == "__main__":
    unittest.main()
