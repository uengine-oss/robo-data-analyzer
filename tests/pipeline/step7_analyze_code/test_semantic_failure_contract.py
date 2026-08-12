# -*- coding: utf-8 -*-
"""모델 품질 finding은 격리·표면화하고 운영/schema 호출 경계는 명시 전파한다."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import (
    AnalysisContext,
    ReferencedSymbolContext,
    StatementFact,
)
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    BatchSummaryItem,
    BatchSummaryOutput,
    RuleItem,
    RulesExamplesOutput,
    SummaryOutput,
)
from pipeline.steps.step7_analyze_code.batch_analyze import (
    _apply_batch_summary,
    _apply_node_analysis,
    _collect_keyed_concern,
    _direct_table_node_ids,
    _is_summary_terminal,
    _partition_keyed_items,
)
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import analyze_code_three_split
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore
from pipeline.models.analysis_context import ControlSubtreeNode
from llm.runners.model_output_error import ModelOutputError


class DbmsKeyContractTest(unittest.IsolatedAsyncioTestCase):
    def test_insufficient_dbms_summary_is_not_published(self) -> None:
        node = CodeNode(
            id="fn", name="work", signature="work()", file_path="x.sql",
            start_line=1, end_line=1, label="PROCEDURE", summary="stale",
            stereotype="Helper",
        )
        context = AnalysisContext(
            id="fn", signature="work()", file_path="x.sql",
            start_line=1, end_line=1, summary="stale",
        )
        output = BatchSummaryItem(
            node_key="1-1", summary="", stereotype="",
            evidence_status="insufficient",
            missing_context=["호출 대상 계약 원문"], questions=[],
        )

        finding = _apply_batch_summary(
            output, node, context, SimpleNamespace(name="dbms"), {"fn": context},
        )

        self.assertEqual("unverified", finding.status)
        self.assertIsNone(node.summary)
        self.assertIsNone(node.stereotype)
        self.assertEqual("", context.summary)
        self.assertEqual("needs_context", context.extra["summary_status"])
        self.assertEqual(
            ["호출 대상 계약 원문"], node.extra["summary_missing_context"],
        )

    def test_table_insight_schedules_only_direct_rw_owner(self) -> None:
        routine = AnalysisContext(
            id="routine", signature="p", file_path="p.sql",
            start_line=1, end_line=20,
            reads=("db:T",),
        )
        select = AnalysisContext(
            id="select", signature="SELECT", file_path="p.sql",
            start_line=5, end_line=5,
            reads=("db:T",), direct_reads=("db:T",),
        )

        self.assertEqual(
            ["select"],
            _direct_table_node_ids(
                ["routine", "select"],
                {"routine": routine, "select": select},
            ),
        )

    def test_only_trusted_or_explicitly_rejected_summary_is_terminal(self) -> None:
        node = CodeNode(
            id="fn", name="fn", signature="fn", file_path="x.sql",
            start_line=1, end_line=1, label="FUNCTION",
        )
        context = AnalysisContext(
            id="fn", signature="fn", file_path="x.sql",
            start_line=1, end_line=1,
        )
        self.assertFalse(_is_summary_terminal(node, context))
        context.extra["summary_status"] = "unavailable"
        self.assertFalse(_is_summary_terminal(node, context))
        context.extra["summary_status"] = "rejected"
        self.assertTrue(_is_summary_terminal(node, context))
        context.extra.clear()
        node.summary = "검증된 요약"
        self.assertTrue(_is_summary_terminal(node, context))

    def test_rule_serialization_uses_owning_routine_table_scope(self) -> None:
        routine = CodeNode(
            id="routine", name="p", signature="p", file_path="p.sql",
            start_line=1, end_line=40, label="FUNCTION",
        )
        child = CodeNode(
            id="child", name="assignment", signature="assignment",
            file_path="p.sql", start_line=28, end_line=28,
            label="ASSIGNMENT", parent_id=routine.id,
        )
        routine_ctx = AnalysisContext(
            id=routine.id, signature="p", file_path="p.sql",
            start_line=1, end_line=40,
            reads=("db:RDITAG_TB",),
            extra={"table_op": {"db:RDITAG_TB": "READ"}},
        )
        child_ctx = AnalysisContext(
            id=child.id, signature="assignment", file_path="p.sql",
            start_line=28, end_line=28,
            extra={"rule_rows": (28,)},
        )
        accepted = RuleItem(
            anchor_line=28, cond=None,
            then=['select("RDITAG_TB")'], nl="RDITAG_TB를 조회한다.",
        )
        with (
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.routine_inventory",
                return_value=(routine, object()),
            ),
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.grade_rules",
                return_value=object(),
            ),
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.partition_rule_candidates",
                return_value=([accepted], []),
            ),
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.serialize_rules",
                return_value=([], []),
            ) as serialize,
        ):
            _apply_node_analysis(
                None, BatchRulesItem(node_key="28-28", rules=[]), None,
                child, child_ctx, SimpleNamespace(name="dbms"),
                SourceFileCache(),
                contexts={routine.id: routine_ctx, child.id: child_ctx},
                code_by_id={routine.id: routine, child.id: child},
                requested_parts={"rules"},
            )

        self.assertEqual(("db:RDITAG_TB",), serialize.call_args.args[2])
        self.assertEqual((), serialize.call_args.args[3])
        self.assertEqual(
            {"db:RDITAG_TB": "READ"}, serialize.call_args.args[4],
        )

    def test_duplicate_and_unknown_keys_leave_expected_ids_unresolved(self) -> None:
        expected = {"1-2": "a", "3-4": "b"}
        items = [
            BatchSummaryItem(node_key="1-2", summary="a1", stereotype="Helper", evidence_status="sufficient"),
            BatchSummaryItem(node_key="1-2", summary="a2", stereotype="Helper", evidence_status="sufficient"),
            BatchSummaryItem(node_key="3-4", summary="b", stereotype="Helper", evidence_status="sufficient"),
            BatchSummaryItem(node_key="9-9", summary="unknown", stereotype="Helper", evidence_status="sufficient"),
        ]
        accepted, unresolved = _partition_keyed_items(
            items, expected, concern="summary",
        )
        self.assertEqual({"b"}, set(accepted))
        self.assertEqual({"a"}, unresolved)

    def test_invalid_rule_leaf_keeps_ownership_key_for_row_grading(self) -> None:
        from pipeline.models.code_analysis_output import BatchRulesItem

        expected = {"1-2": "a"}
        broken = RuleItem(anchor_line=2, cond='F("flag") ==', then=[])
        accepted, unresolved = _partition_keyed_items(
            [BatchRulesItem(node_key="1-2", rules=[broken])],
            expected,
            concern="rules",
        )
        self.assertEqual({"a"}, set(accepted))
        self.assertEqual(set(), unresolved)

    async def test_missing_key_fails_in_schema_after_one_model_call(self) -> None:
        contexts = {
            "a": SimpleNamespace(start_line=1, end_line=2),
            "b": SimpleNamespace(start_line=3, end_line=4),
        }
        async def invalid_output(*, response_model, **_kwargs):
            return response_model.model_validate({
                "nodes": {
                    "1-2": {
                        "node_key": "1-2",
                        "summary": "a",
                        "stereotype": "Helper",
                        "evidence_status": "sufficient",
                        "missing_context": [],
                        "questions": [],
                    },
                },
            })
        with (
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze._build_batch_values",
                return_value=({"unit_id": "unit"}, {}),
            ),
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.run_yaml_rule",
                side_effect=invalid_output,
            ) as run,
        ):
            with self.assertRaises(ValidationError):
                await _collect_keyed_concern(
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
                    llm_slots=PrioritySemaphore(1),
                    strategy_name="dbms",
                )
        self.assertEqual(1, run.await_count)

    async def test_empty_rules_are_visible_findings_not_silent_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.c"
            source.write_text('if (flag == "1")\n{\n  act();\n}\n', encoding="utf-8")
            node = CodeNode(
                id="fn", name="work", signature="work()", file_path=str(source),
                start_line=1, end_line=4, label="FUNCTION",
            )
            context = AnalysisContext(
                id="fn", signature="work()", file_path=str(source),
                start_line=1, end_line=4,
                extra={"control_subtree": [ControlSubtreeNode(
                    node_id="if", ntype="IF", start=1, end=4, expression='flag == "1"',
                )]},
            )
            context.extra.setdefault("ast_statements", ())
            # spec 117 — 채점이 기대하는 의무 행은 run 시작 때 배분된 값이다.
            context.extra["rule_rows"] = (1,)
            _apply_node_analysis(
                None,
                BatchRulesItem(node_key="1-4", rules=[]),
                None,
                node,
                context,
                SimpleNamespace(name="dbms"),
                SourceFileCache(),
                contexts={"fn": context},
                code_by_id={"fn": node},
                requested_parts={"rules"},
            )
            self.assertEqual([], node.extra["rules"])
            self.assertEqual("completed_with_findings", node.extra["analysis_status"])
            self.assertEqual("unavailable", node.extra["analysis_findings"][0]["status"])
            self.assertEqual([1], node.extra["analysis_findings"][0]["coordinates"])

    async def test_invalid_stereotype_does_not_partially_mutate_dbms_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("SELECT result FROM source_table;\n", encoding="utf-8")
            node = CodeNode(
                id="fn", name="work", signature="work()", file_path=str(source),
                start_line=1, end_line=1, label="FUNCTION",
                summary="before",
            )
            context = AnalysisContext(
                id="fn", signature="work()", file_path=str(source),
                start_line=1, end_line=1,
                summary="context before",
            )
            context.extra.setdefault("ast_statements", ())
            output = BatchSummaryItem.model_construct(
                node_key="1-1", summary="after",
                stereotype="InventedRole", evidence_status="sufficient",
                missing_context=[], questions=[],
            )
            with self.assertRaisesRegex(ValueError, "invalid dbms stereotype"):
                _apply_batch_summary(
                    output, node, context, SimpleNamespace(name="dbms"),
                    {"fn": context},
                )
        self.assertEqual(("before", None), (node.summary, node.stereotype))
        self.assertEqual("context before", context.summary)

    async def test_exact_callee_copy_is_not_published_by_dbms(self) -> None:
        node = CodeNode(
            id="caller", name="caller", signature="caller()", file_path="x.sql",
            start_line=1, end_line=1, label="PROCEDURE",
        )
        context = AnalysisContext(
            id="caller", signature="caller()", file_path="x.sql",
            start_line=1, end_line=1, callees=("callee",),
        )
        callee = AnalysisContext(
            id="callee", signature="callee()", file_path="x.sql",
            start_line=2, end_line=2, summary="계좌번호 정합성을 검증한다.",
        )
        output = BatchSummaryItem(
            node_key="1-1",
            summary="계좌번호 정합성을 검증한다.",
            stereotype="Helper",
            evidence_status="sufficient",
            missing_context=[],
            questions=[],
        )

        finding = _apply_batch_summary(
            output, node, context, SimpleNamespace(name="dbms"),
            {"caller": context, "callee": callee},
        )

        self.assertIsNotNone(finding)
        self.assertEqual("summary", finding.concern)
        self.assertEqual("rejected", finding.status)
        self.assertIsNone(node.summary)
        self.assertIsNone(node.stereotype)
        self.assertEqual([], node.extra["questions"])
        self.assertEqual("", context.summary)
        self.assertEqual("rejected", context.extra["summary_status"])

    async def test_explicit_comparison_operand_substitution_is_not_published(self) -> None:
        node = CodeNode(
            id="routine", name="routine", signature="routine(IN_TAGSN)",
            file_path="x.sql", start_line=1, end_line=2, label="PROCEDURE",
        )
        context = AnalysisContext(
            id="routine", signature="routine(IN_TAGSN)", file_path="x.sql",
            start_line=1, end_line=2,
            referenced_symbols=(
                ReferencedSymbolContext(
                    id="parameter", name="IN_TAGSN", kind="parameter",
                ),
                ReferencedSymbolContext(
                    id="count", name="TAG_CNT", kind="local",
                ),
            ),
        )
        output = BatchSummaryItem(
            node_key="1-2",
            summary="대상이 존재하고(IN_TAGSN >= 1) 후속 작업을 수행한다.",
            stereotype="Helper", evidence_status="sufficient",
            missing_context=[], questions=[],
        )

        finding = _apply_batch_summary(
            output, node, context, SimpleNamespace(name="dbms"),
            {"routine": context}, evidence_text="IF TAG_CNT >= 1 THEN",
        )

        self.assertIsNotNone(finding)
        self.assertEqual("rejected", finding.status)
        self.assertIn("IN_TAGSN >= 1", finding.reasons[0])
        self.assertIsNone(node.summary)
        self.assertEqual("", context.summary)
        self.assertEqual("rejected", context.extra["summary_status"])


class FrameworkFailureContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_insufficient_framework_summary_is_not_published(self) -> None:
        node = CodeNode(
            id="fn", name="work", signature="work()", file_path="x.py",
            start_line=1, end_line=1, label="FUNCTION", summary="stale",
            stereotype="Helper",
        )
        context = AnalysisContext(
            id="fn", signature="work()", file_path="x.py",
            start_line=1, end_line=1, summary="stale",
        )
        context.extra["ast_statements"] = ()
        output = SummaryOutput(
            summary="", stereotype="", evidence_status="insufficient",
            missing_context=["함수 본문"], questions=[],
        )

        result = await analyze_code_three_split(
            "fn", {"fn": context}, {"fn": node},
            SimpleNamespace(name="framework", prompts_dir="."),
            source_cache=SourceFileCache(), precomputed_summary=output,
            precomputed_summary_supplied=True,
            llm_slots=PrioritySemaphore(1),
        )

        self.assertIs(result, node)
        self.assertIsNone(node.summary)
        self.assertIsNone(node.stereotype)
        self.assertEqual("", context.summary)
        self.assertEqual("needs_context", context.extra["summary_status"])
        self.assertEqual("unverified", node.extra["analysis_findings"][0]["status"])

    async def test_exact_callee_copy_is_not_published_by_framework(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.c"
            source.write_text("return 0;\n", encoding="utf-8")
            node = CodeNode(
                id="caller", name="caller", signature="caller()",
                file_path=str(source), start_line=1, end_line=1,
                label="FUNCTION",
            )
            context = AnalysisContext(
                id="caller", signature="caller()", file_path=str(source),
                start_line=1, end_line=1, callees=("callee",),
            )
            context.extra.setdefault("ast_statements", ())
            callee = AnalysisContext(
                id="callee", signature="callee()", file_path=str(source),
                start_line=1, end_line=1,
                summary="계좌번호 정합성을 검증한다.",
            )
            output = SummaryOutput(
                summary="계좌번호 정합성을 검증한다.",
                stereotype="Helper",
                evidence_status="sufficient",
                missing_context=[],
                questions=[],
            )

            async def no_rules(*, response_model, **_kwargs):
                return response_model.model_validate({"rules": []})

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=no_rules,
            ):
                result = await analyze_code_three_split(
                    "caller", {"caller": context, "callee": callee},
                    {"caller": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                    precomputed_summary=output,
                    precomputed_summary_supplied=True,
                    llm_slots=PrioritySemaphore(1),
                )

        self.assertIs(result, node)
        self.assertIsNone(node.summary)
        self.assertIsNone(node.stereotype)
        self.assertEqual([], node.extra["questions"])
        self.assertEqual("", context.summary)
        self.assertEqual("completed_with_findings", node.extra["analysis_status"])
        self.assertEqual("summary", node.extra["analysis_findings"][0]["concern"])
        self.assertEqual("rejected", node.extra["analysis_findings"][0]["status"])

    async def test_model_output_failures_become_visible_findings_and_run_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.c"
            source.write_text("return 0;\n", encoding="utf-8")
            node = CodeNode(
                id="fn", name="work", signature="work()", file_path=str(source),
                start_line=1, end_line=1, label="FUNCTION",
            )
            context = AnalysisContext(
                id="fn", signature="work()", file_path=str(source),
                start_line=1, end_line=1,
            )
            context.extra["ast_statements"] = (
                StatementFact("r1", "RETURN", 1, 1, expression="0"),
            )
            calls = []

            async def unavailable(*, rule_name, **_kwargs):
                calls.append(rule_name)
                raise ModelOutputError(
                    rule_name=rule_name,
                    kind="schema",
                    detail="bad generated payload",
                    original_type="ValidationError",
                )

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=unavailable,
            ):
                result = await analyze_code_three_split(
                    "fn", {"fn": context}, {"fn": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                    llm_slots=PrioritySemaphore(1),
                )

        self.assertIs(result, node)
        self.assertEqual(["code_summary", "code_rules_examples"], calls)
        self.assertIsNone(node.summary)
        self.assertEqual([], node.extra["rules"])
        self.assertEqual("completed_with_findings", node.extra["analysis_status"])
        self.assertEqual(
            {"summary", "rules"},
            {finding["concern"] for finding in node.extra["analysis_findings"]},
        )

    async def test_invalid_stereotype_does_not_partially_mutate_framework_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.py"
            source.write_text("def work():\n    return 0\n", encoding="utf-8")
            node = CodeNode(
                id="fn", name="work", signature="work()", file_path=str(source),
                start_line=1, end_line=2, label="FUNCTION",
                summary="before",
            )
            context = AnalysisContext(
                id="fn", signature="work()", file_path=str(source),
                start_line=1, end_line=2,
                summary="context before",
            )
            context.extra.setdefault("ast_statements", ())
            output = SummaryOutput.model_construct(
                summary="after", stereotype="InventedRole",
                evidence_status="sufficient", missing_context=[], questions=[],
            )
            with self.assertRaisesRegex(ValueError, "invalid framework stereotype"):
                await analyze_code_three_split(
                    "fn", {"fn": context}, {"fn": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                    precomputed_summary=output,
                    precomputed_summary_supplied=True,
                    llm_slots=PrioritySemaphore(1),
                )
        self.assertEqual(("before", None), (node.summary, node.stereotype))
        self.assertEqual("context before", context.summary)

    async def test_summary_failure_propagates_after_one_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.c"
            source.write_text("int work(void) { return 0; }\n", encoding="utf-8")
            node = CodeNode(
                id="fn", name="work", signature="work()", file_path=str(source),
                start_line=1, end_line=1, label="FUNCTION",
            )
            context = AnalysisContext(
                id="fn", signature="work()", file_path=str(source),
                start_line=1, end_line=1,
            )
            context.extra.setdefault("ast_statements", ())
            run = AsyncMock(side_effect=ValueError("summary invalid"))
            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                new=run,
            ):
                with self.assertRaisesRegex(ValueError, "summary invalid"):
                    await analyze_code_three_split(
                        "fn", {"fn": context}, {"fn": node},
                        SimpleNamespace(name="framework", prompts_dir="."),
                        source_cache=SourceFileCache(),
                        llm_slots=PrioritySemaphore(1),
                    )
        self.assertEqual(1, run.await_count)

    async def test_rules_failure_does_not_regenerate_summary_or_store_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.py"
            source.write_text("def work():\n    return 0\n", encoding="utf-8")
            node = CodeNode(
                id="fn", name="work", signature="work()", file_path=str(source),
                start_line=1, end_line=2, label="FUNCTION",
            )
            context = AnalysisContext(
                id="fn", signature="work()", file_path=str(source),
                start_line=1, end_line=2,
            )
            context.extra["ast_statements"] = (
                StatementFact("r2", "RETURN", 2, 2, expression="0"),
            )
            calls = []

            async def fake_rule(*, rule_name, response_model, **_kwargs):
                calls.append(rule_name)
                if rule_name == "code_summary":
                    return response_model.model_validate({
                        "result": {
                            "summary": "작업을 수행해 그 결과를 호출자에게 반환한다.",
                            "stereotype": "Helper",
                            "evidence_status": "sufficient",
                            "missing_context": [],
                            "questions": [],
                        },
                    })
                raise ValueError("rules invalid")

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=fake_rule,
            ):
                with self.assertRaisesRegex(ValueError, "rules invalid"):
                    await analyze_code_three_split(
                        "fn", {"fn": context}, {"fn": node},
                        SimpleNamespace(name="framework", prompts_dir="."),
                        source_cache=SourceFileCache(),
                        llm_slots=PrioritySemaphore(1),
                    )
        self.assertEqual(1, calls.count("code_summary"))
        self.assertEqual(1, calls.count("code_rules_examples"))
        self.assertNotIn("rules", node.extra)
        self.assertEqual([], node.extra["questions"])


if __name__ == "__main__":
    unittest.main()
