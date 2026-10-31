from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from graph.schema.graph_nodes import CodeNode
from graph.nodes.code_node import to_summary_item, update_summaries
from pipeline.execution.graph_delta_stream import _node_props
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    BatchSummaryItem,
    BatchSummaryOutput,
    ExampleItem,
    RuleItem,
    RulesExamplesOutput,
    SummaryOutput,
)
from pipeline.steps.step7_analyze_code.batch_analyze import (
    _analyze_batch,
    _collect_keyed_concern,
    _partition_keyed_items,
)
from pipeline.steps.step7_analyze_code.batch_planner import Batch
from pipeline.steps.step7_analyze_code.concern_retry import run_concern_with_retry
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    analyze_code_three_split,
)
from shared.files.cache import SourceFileCache


class ConcernRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_is_never_called_again(self) -> None:
        call = AsyncMock(return_value="first")
        result = await run_concern_with_retry(
            component="test",
            concern="summary",
            target_id="node",
            call=call,
        )
        self.assertEqual("first", result.value)
        self.assertEqual(1, result.attempts)
        self.assertEqual(1, call.await_count)

    async def test_only_failed_concern_is_called_twice(self) -> None:
        call = AsyncMock(side_effect=[ValueError("first"), "second"])
        result = await run_concern_with_retry(
            component="test",
            concern="rules",
            target_id="node",
            call=call,
        )
        self.assertEqual("second", result.value)
        self.assertEqual(2, result.attempts)
        self.assertEqual(2, call.await_count)

    async def test_exhausted_failure_remains_explicit(self) -> None:
        call = AsyncMock(side_effect=ValueError("no result"))
        result = await run_concern_with_retry(
            component="test",
            concern="insights",
            target_id="node",
            call=call,
        )
        self.assertIsNone(result.value)
        self.assertIsInstance(result.error, ValueError)
        self.assertEqual(2, call.await_count)


class DbmsKeyContractTest(unittest.IsolatedAsyncioTestCase):
    def test_duplicate_and_unknown_keys_do_not_replace_unique_success(self) -> None:
        expected = {"1-2": "a", "3-4": "b"}
        items = [
            BatchSummaryItem(node_key="1-2", summary="a1", search_text="a1 search"),
            BatchSummaryItem(node_key="1-2", summary="a2", search_text="a2 search"),
            BatchSummaryItem(node_key="3-4", summary="b", search_text="b search"),
            BatchSummaryItem(node_key="9-9", summary="unknown", search_text="unknown search"),
        ]
        accepted, unresolved = _partition_keyed_items(
            items, expected, concern="summary", attempt=1,
        )
        self.assertEqual({"b"}, set(accepted))
        self.assertEqual({"a"}, unresolved)

    def test_invalid_rule_item_does_not_discard_valid_sibling(self) -> None:
        expected = {"1-2": "a", "3-4": "b"}
        rule = RuleItem(
            statement="허용되면 반환한다.",
            anchor_line=2,
            examples=[ExampleItem(given="허용", when="요청", then="반환")],
        )
        items = [
            BatchRulesItem(node_key="1-2", rules=[rule, rule]),
            BatchRulesItem(node_key="3-4", rules=[rule]),
        ]
        accepted, unresolved = _partition_keyed_items(
            items, expected, concern="rules", attempt=1,
        )
        self.assertEqual({"b"}, set(accepted))
        self.assertEqual({"a"}, unresolved)

    async def test_only_missing_key_is_focused_on_retry(self) -> None:
        contexts = {
            "a": SimpleNamespace(start_line=1, end_line=2),
            "b": SimpleNamespace(start_line=3, end_line=4),
        }
        first = BatchSummaryOutput(nodes=[
            BatchSummaryItem(node_key="1-2", summary="a", search_text="a search"),
        ])
        second = BatchSummaryOutput(nodes=[
            BatchSummaryItem(node_key="3-4", summary="b", search_text="b search"),
        ])
        requested: list[tuple[str, ...]] = []

        def build_values(batch, *_args, **_kwargs):
            requested.append(batch.node_ids)
            return {"unit_id": "unit"}, {}

        with (
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze._build_batch_values",
                side_effect=build_values,
            ),
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.run_yaml_rule",
                new=AsyncMock(side_effect=[first, second]),
            ) as run,
        ):
            accepted, unresolved = await _collect_keyed_concern(
                parent_id="parent",
                node_ids=["a", "b"],
                concern="summary",
                rule_name="code_summary_batch",
                response_model=BatchSummaryOutput,
                prompts_dir=Path("."),
                contexts=contexts,
                code_by_id={},
                cost_budget=None,
                codebase_notes="",
                module_intents={},
                table_descriptions={},
                source_cache=SourceFileCache(),
            )

        self.assertEqual({"a", "b"}, set(accepted))
        self.assertEqual(set(), unresolved)
        self.assertEqual([("a", "b"), ("b",)], requested)
        self.assertEqual(2, run.await_count)

    async def test_non_applicable_insights_are_not_called_and_rule_failure_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("IF flag = 1 THEN NULL; END IF;\n", encoding="utf-8")
            node = CodeNode(
                id="a",
                signature="IF",
                file_path=str(source),
                start_line=1,
                end_line=1,
                label="IF",
            )
            context = AnalysisContext(
                id="a",
                signature="IF",
                file_path=str(source),
                start_line=1,
                end_line=1,
            )
            concerns: list[str] = []

            async def collect(**kwargs):
                concern = kwargs["concern"]
                concerns.append(concern)
                if concern == "summary":
                    return {
                        "a": BatchSummaryItem(
                            node_key="1-1",
                            summary="조건에 따라 실행 경로를 선택한다.",
                            search_text="flag 값에 따른 조건 분기",
                        )
                    }, set()
                if concern == "rules":
                    return {}, {"a"}
                raise AssertionError(f"unexpected concern: {concern}")

            with patch(
                "pipeline.steps.step7_analyze_code.batch_analyze._collect_keyed_concern",
                side_effect=collect,
            ):
                result = await _analyze_batch(
                    Batch("parent", ("a",)),
                    {"a": context},
                    {"a": node},
                    SimpleNamespace(name="dbms", prompts_dir="."),
                    None,
                    "",
                    {},
                    {},
                    SourceFileCache(),
                    {"a"},
                    set(),
                )

        self.assertEqual(["summary", "rules"], concerns)
        self.assertEqual(1, len(result))
        self.assertEqual(("rules",), result[0].extra["partial"])
        self.assertEqual([], result[0].extra["rules"])


class FrameworkConcernContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_summary_exhaustion_does_not_regenerate_or_block_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.c"
            source.write_text("int work(void) { return 0; }\n", encoding="utf-8")
            node = CodeNode(
                id="fn",
                name="work",
                signature="work()",
                file_path=str(source),
                start_line=1,
                end_line=1,
                label="FUNCTION",
            )
            context = AnalysisContext(
                id="fn",
                signature="work()",
                file_path=str(source),
                start_line=1,
                end_line=1,
            )
            calls: list[str] = []

            async def fake_rule(*, rule_name, **_kwargs):
                calls.append(rule_name)
                if rule_name == "code_summary":
                    raise ValueError("summary unavailable")
                if rule_name == "code_rules_examples":
                    return RulesExamplesOutput()
                raise AssertionError(f"unexpected rule: {rule_name}")

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=fake_rule,
            ):
                result = await analyze_code_three_split(
                    "fn",
                    {"fn": context},
                    {"fn": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                )

        self.assertIs(result, node)
        self.assertEqual(2, calls.count("code_summary"))
        self.assertEqual(1, calls.count("code_rules_examples"))
        self.assertNotIn("code_table_insights", calls)
        self.assertEqual(("summary",), node.extra["partial"])
        self.assertEqual([], node.extra["rules"])

    async def test_rules_retry_does_not_regenerate_successful_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.py"
            source.write_text("def work():\n    return 0\n", encoding="utf-8")
            node = CodeNode(
                id="fn",
                name="work",
                signature="work()",
                file_path=str(source),
                start_line=1,
                end_line=2,
                label="FUNCTION",
            )
            context = AnalysisContext(
                id="fn",
                signature="work()",
                file_path=str(source),
                start_line=1,
                end_line=2,
            )
            calls: list[str] = []

            async def fake_rule(*, rule_name, **_kwargs):
                calls.append(rule_name)
                if rule_name == "code_summary":
                    return SummaryOutput(summary="작업 결과를 반환한다.", search_text="작업 결과 반환")
                if calls.count("code_rules_examples") == 1:
                    raise ValueError("rules unavailable")
                return RulesExamplesOutput()

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=fake_rule,
            ):
                result = await analyze_code_three_split(
                    "fn",
                    {"fn": context},
                    {"fn": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                )

        self.assertIs(result, node)
        self.assertEqual(1, calls.count("code_summary"))
        self.assertEqual(2, calls.count("code_rules_examples"))
        self.assertNotIn("partial", node.extra)


class PartialStateTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_summary_failure_state_is_persisted_and_streamed_without_filling_meaning(self) -> None:
        node = CodeNode(
            id="fn",
            signature="work()",
            file_path="sample.c",
            start_line=1,
            end_line=2,
            label="FUNCTION",
            extra={"partial": ("summary", "insights")},
        )
        self.assertEqual(
            ["summary", "insights"],
            to_summary_item(node)["analysis_partial"],
        )
        self.assertEqual(
            ["summary", "insights"],
            _node_props(node)["analysis_partial"],
        )
        self.assertNotIn("summary", to_summary_item(node))

        client = SimpleNamespace(run_batch_unwind=AsyncMock())
        await update_summaries(client, [node])
        # 역할은 속성으로만 — 라벨을 붙이는 두 번째 쓰기가 없다 (Ontological DB 호환).
        self.assertEqual(1, client.run_batch_unwind.await_count)
        items = client.run_batch_unwind.await_args.args[1]
        self.assertEqual(["summary", "insights"], items[0]["analysis_partial"])


if __name__ == "__main__":
    unittest.main()
