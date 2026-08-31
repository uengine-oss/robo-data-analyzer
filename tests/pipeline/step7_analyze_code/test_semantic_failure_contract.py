# -*- coding: utf-8 -*-
"""모델 품질 finding은 격리·표면화하고 운영/schema 호출 경계는 명시 전파한다."""
from __future__ import annotations

import asyncio
import json
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
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    BatchSummaryItem,
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
from pipeline.models.summary_response_schema import (
    frame_summary_response_model,
    normalize_frame_summary_response,
)
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import analyze_code_three_split
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore
from pipeline.models.analysis_context import ControlSubtreeNode
from llm.runners.model_output_error import ModelOutputError


def _frame_summary(
    node: CodeNode,
    context: AnalysisContext,
    *,
    summary: str,
    stereotype: str,
    status: str = "sufficient",
) -> SummaryOutput:
    profile, specs = build_code_semantic_plan(node, context)
    summary_specs = semantic_concern_slots(specs)["summary"]
    model = frame_summary_response_model("dbms", summary_specs)
    parsed = model.model_validate({
        "summary": summary if status != "insufficient" else "",
        "stereotype": stereotype or "Helper",
    })
    return normalize_frame_summary_response(
        parsed, profile, summary_specs,
    )


def _batch_frame_summary(
    node_key: str,
    node: CodeNode,
    context: AnalysisContext,
    **kwargs,
) -> BatchSummaryItem:
    summary = _frame_summary(node, context, **kwargs)
    return BatchSummaryItem(
        node_key=node_key,
        summary=summary.summary,
        stereotype=summary.stereotype,
        evidence_status=summary.evidence_status,
        missing_context=summary.missing_context,
        semantic_frame=summary.semantic_frame,
    )


def _frame_wire_payload(
    response_model,
    *,
    meaning: str,
    stereotype: str,
) -> dict:
    return {
        "summary": meaning,
        "stereotype": stereotype,
    }


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
        context.extra["ast_statements"] = ()
        output = _batch_frame_summary(
            "1-1", node, context,
            summary="", stereotype="", status="insufficient",
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
            ("whole-target summary returned no meaning",),
            finding.reasons,
        )

    def test_table_usage_meaning_schedules_only_direct_rw_owner(self) -> None:
        routine = AnalysisContext(
            id="routine", signature="p", file_path="p.sql",
            start_line=1, end_line=20,
            reads=("db:T",),
        )
        select = AnalysisContext(
            id="select", signature="SELECT", file_path="p.sql",
            start_line=5, end_line=5,
            reads=("db:T",), direct_reads=("db:T",),
            data_object_usages=(DataObjectUsageEvidence(
                source_id="select",
                raw_reference="db:T",
                name="T",
                access="READ",
                evidence_file="p.sql",
                evidence_line=5,
                producer="linked_edges",
                table_id="db:T",
                resolution="exact",
            ),),
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
            anchor_line=28, structural_condition=None,
            structural_effects=['select("RDITAG_TB")'],
        )
        with (
            patch(
                "pipeline.steps.step7_analyze_code.batch_analyze.serialize_rules",
                return_value=([], []),
            ) as serialize,
        ):
            _apply_node_analysis(
                None, BatchRulesItem(
                    node_key="28-28", rules=[accepted],
                ), None,
                child, child_ctx, SimpleNamespace(name="dbms"),
                SourceFileCache(),
                contexts={routine.id: routine_ctx, child.id: child_ctx},
                code_by_id={routine.id: routine, child.id: child},
                requested_parts={"rules"},
            )

        self.assertEqual(("db:RDITAG_TB",), serialize.call_args.args[1])
        self.assertEqual((), serialize.call_args.args[2])
        self.assertEqual(
            {"db:RDITAG_TB": "READ"}, serialize.call_args.args[3],
        )

    def test_unverified_rule_is_not_regraded_or_serialized_as_trusted(self) -> None:
        routine = CodeNode(
            id="routine", name="p", signature="p", file_path="p.sql",
            start_line=1, end_line=4, label="FUNCTION",
        )
        context = AnalysisContext(
            id=routine.id, signature="p", file_path="p.sql",
            start_line=1, end_line=4,
            extra={"rule_rows": (1,)},
        )
        finding = AnalysisFinding(
            concern="rules",
            status="unverified",
            target_id="routine::R-1",
            coordinates=(1,),
            reasons=("semantic comparison required",),
        )
        with patch(
            "pipeline.steps.step7_analyze_code.batch_analyze.serialize_rules",
            return_value=([], []),
        ) as serialize:
            _apply_node_analysis(
                None,
                BatchRulesItem(node_key="1-4", rules=[]),
                None,
                routine,
                context,
                SimpleNamespace(name="dbms"),
                SourceFileCache(),
                contexts={routine.id: context},
                code_by_id={routine.id: routine},
                requested_parts={"rules"},
                pre_findings=(finding,),
            )

        self.assertEqual([], context.extra["rules"])
        self.assertEqual([1], routine.extra["rule_grading"]["finding_rows"])
        self.assertEqual([], routine.extra["rule_grading"]["accepted_rows"])
        self.assertEqual([], serialize.call_args.args[0])

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
        broken = RuleItem(
            anchor_line=2,
            structural_condition='F("flag") ==',
            structural_effects=[],
        )
        accepted, unresolved = _partition_keyed_items(
            [BatchRulesItem(node_key="1-2", rules=[broken])],
            expected,
            concern="rules",
        )
        self.assertEqual({"a"}, set(accepted))
        self.assertEqual(set(), unresolved)

    async def test_missing_fragment_slot_fails_in_schema_after_one_model_call(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "x.sql"
        source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        contexts = {
            "a": AnalysisContext(id="a", signature="a", file_path=str(source), start_line=1, end_line=2),
            "b": AnalysisContext(id="b", signature="b", file_path=str(source), start_line=3, end_line=4),
        }
        code_by_id = {
            node_id: CodeNode(
                id=node_id, signature=node_id, file_path=str(source),
                start_line=context.start_line, end_line=context.end_line,
                label="PROCEDURE",
            )
            for node_id, context in contexts.items()
        }
        for node_id, context in contexts.items():
            context.extra["ast_statements"] = ()
            profile, specs = build_code_semantic_plan(
                code_by_id[node_id], context,
            )
            context.extra["_semantic_frame_plan"] = (
                profile, specs, semantic_concern_slots(specs),
            )
        async def invalid_output(*, response_model, **_kwargs):
            return response_model.model_validate({
                "slots": {},
                "stereotype": "Helper",
            })
        with (
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
                    rule_name="code_summary_node",
                    prompts_dir=Path("."),
                    contexts=contexts,
                    code_by_id=code_by_id,
                    cost_budget=None,
                    table_descriptions={},
                    source_cache=SourceFileCache(),
                    llm_slots=PrioritySemaphore(1),
                    strategy_name="dbms",
                )
        self.assertEqual(1, run.await_count)

    async def test_dbms_sibling_summaries_use_available_shared_slots(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "siblings.sql"
        source.write_text("first\nsecond\n", encoding="utf-8")
        contexts = {
            node_id: AnalysisContext(
                id=node_id, signature=node_id, file_path=str(source),
                start_line=line, end_line=line,
            )
            for node_id, line in (("a", 1), ("b", 2))
        }
        code_by_id = {
            node_id: CodeNode(
                id=node_id, signature=node_id, file_path=str(source),
                start_line=context.start_line, end_line=context.end_line,
                label="PROCEDURE",
            )
            for node_id, context in contexts.items()
        }
        for node_id, context in contexts.items():
            context.extra["ast_statements"] = ()
            profile, specs = build_code_semantic_plan(code_by_id[node_id], context)
            context.extra["_semantic_frame_plan"] = (
                profile, specs, semantic_concern_slots(specs),
            )

        started = 0
        in_flight = 0
        peak_in_flight = 0
        both_started = asyncio.Event()

        async def valid_output(*, response_model, **_kwargs):
            nonlocal started, in_flight, peak_in_flight
            started += 1
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            try:
                return response_model.model_validate({
                    "summary": "해당 원문을 처리한다",
                    "stereotype": "Helper",
                })
            finally:
                in_flight -= 1

        with patch(
            "pipeline.steps.step7_analyze_code.batch_analyze.run_yaml_rule",
            side_effect=valid_output,
        ) as run:
            result = await _collect_keyed_concern(
                parent_id="parent",
                node_ids=["a", "b"],
                concern="summary",
                rule_name="code_summary_node",
                prompts_dir=Path("."),
                contexts=contexts,
                code_by_id=code_by_id,
                cost_budget=None,
                table_descriptions={},
                source_cache=SourceFileCache(),
                llm_slots=PrioritySemaphore(2),
                strategy_name="dbms",
            )

        self.assertEqual({"a", "b"}, set(result))
        self.assertEqual(2, run.await_count)
        self.assertEqual(2, peak_in_flight)

    async def test_dbms_call_node_uses_exact_single_node_source_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "call.sql"
            source_text = "do_work('A\\tB');"
            source.write_text(source_text, encoding="utf-8", newline="")
            node = CodeNode(
                id="call", signature="do_work('A\\tB')", file_path=str(source),
                start_line=1, end_line=1, label="CALL", name="do_work",
            )
            context = AnalysisContext(
                id=node.id, signature=node.signature, file_path=node.file_path,
                start_line=1, end_line=1,
            )
            context.extra["ast_statements"] = ()
            profile, specs = build_code_semantic_plan(node, context)
            context.extra["_semantic_frame_plan"] = (
                profile, specs, semantic_concern_slots(specs),
            )

            async def valid_output(*, rule_name, user_values, response_model, **_kwargs):
                self.assertEqual("code_summary_node", rule_name)
                self.assertEqual(f"1 | {source_text}", user_values["code_body"])
                self.assertNotIn("QUOTED_LITERAL", user_values["code_body"])
                return response_model.model_validate({
                    "summary": "'A\\tB'를 do_work에 전달해 호출한다",
                    "stereotype": "Helper",
                })

            with (
                patch(
                    "pipeline.steps.step7_analyze_code.batch_analyze.run_yaml_rule",
                    side_effect=valid_output,
                ) as run,
            ):
                result = await _collect_keyed_concern(
                    parent_id=None,
                    node_ids=[node.id],
                    concern="summary",
                    rule_name="code_summary_node",
                    prompts_dir=Path("llm/prompts/dbms"),
                    contexts={node.id: context},
                    code_by_id={node.id: node},
                    cost_budget=None,
                    table_descriptions={},
                    source_cache=SourceFileCache(),
                    llm_slots=PrioritySemaphore(1),
                    strategy_name="dbms",
                )

            self.assertEqual(1, run.await_count)
            self.assertIn("do_work", result[node.id].summary)

    async def test_missing_deterministic_rule_is_a_fatal_invariant(self) -> None:
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
            with self.assertRaisesRegex(ValueError, "deterministic Rule invariant"):
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
            framed = _batch_frame_summary(
                "1-1", node, context, summary="after", stereotype="Helper",
            )
            output = BatchSummaryItem.model_construct(
                **{
                    **framed.model_dump(exclude={"semantic_frame"}),
                    "stereotype": "InventedRole",
                    "semantic_frame": framed.semantic_frame,
                },
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
        context.extra["ast_statements"] = ()
        output = _batch_frame_summary(
            "1-1", node, context,
            summary="계좌번호 정합성을 검증한다.", stereotype="Helper",
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
        self.assertNotIn("questions", node.extra)
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
        context.extra["ast_statements"] = ()
        output = _batch_frame_summary(
            "1-2", node, context,
            summary="대상이 존재하고(IN_TAGSN >= 1) 후속 작업을 수행한다.",
            stereotype="Helper",
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
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "x.py"
        source.write_text("def work(): pass\n", encoding="utf-8")
        node = CodeNode(
            id="fn", name="work", signature="work()", file_path=str(source),
            start_line=1, end_line=1, label="FUNCTION", summary="stale",
            stereotype="Helper",
        )
        context = AnalysisContext(
            id="fn", signature="work()", file_path=str(source),
            start_line=1, end_line=1, summary="stale",
        )
        context.extra["ast_statements"] = ()
        output = _frame_summary(
            node, context, summary="", stereotype="", status="insufficient",
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
        self.assertEqual("insufficient", node.meaning_status)
        self.assertGreater(node.missing_context_count, 0)
        self.assertEqual(64, len(node.missing_context_artifact_hash))
        self.assertNotIn("analysis_findings", node.extra)

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
            output = _frame_summary(
                node, context,
                summary="계좌번호 정합성을 검증한다.", stereotype="Helper",
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
        self.assertNotIn("questions", node.extra)
        self.assertEqual("", context.summary)
        self.assertEqual("insufficient", node.meaning_status)
        self.assertGreater(node.missing_context_count, 0)
        self.assertNotIn("analysis_status", node.extra)
        self.assertNotIn("analysis_findings", node.extra)
        frame = node.extra["_summary_semantic_frame"]
        self.assertTrue(frame.slots)
        self.assertTrue(all(
            slot.status == "insufficient" and slot.meaning == ""
            for slot in frame.slots.values()
        ))

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
        self.assertEqual(["code_summary", "code_rule_meanings"], calls)
        self.assertIsNone(node.summary)
        self.assertEqual([], node.extra["rules"])
        self.assertEqual("insufficient", node.meaning_status)
        self.assertGreater(node.missing_context_count, 0)
        self.assertNotIn("analysis_status", node.extra)
        self.assertNotIn("analysis_findings", node.extra)

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
            framed = _frame_summary(
                node, context, summary="after", stereotype="Helper",
            )
            output = SummaryOutput.model_construct(
                **{
                    **framed.model_dump(exclude={"semantic_frame"}),
                    "stereotype": "InventedRole",
                    "semantic_frame": framed.semantic_frame,
                },
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

    async def test_rule_meanings_use_one_provider_call_without_judge_or_repair(self) -> None:
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
                    return response_model.model_validate(_frame_wire_payload(
                        response_model,
                        meaning="작업을 수행해 그 결과를 호출자에게 반환한다.",
                        stereotype="Helper",
                    ))
                if rule_name == "code_rule_meanings":
                    return response_model.model_validate({
                        "effects": {"E1": "작업 결과를 호출자에게 반환한다"},
                        "rows": {"R2": "business_rule"},
                    })
                raise AssertionError(f"unexpected provider concern: {rule_name}")

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=fake_rule,
            ):
                await analyze_code_three_split(
                    "fn", {"fn": context}, {"fn": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                    llm_slots=PrioritySemaphore(1),
                )
        self.assertEqual(1, calls.count("code_summary"))
        self.assertEqual(1, calls.count("code_rule_meanings"))
        self.assertEqual(["code_summary", "code_rule_meanings"], calls)
        self.assertEqual(1, len(node.extra["rules"]))
        self.assertTrue(node.extra["rules"][0]["business_effects"])
        self.assertNotIn("questions", node.extra)


if __name__ == "__main__":
    unittest.main()
