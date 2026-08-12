"""Assigned rule coordinates are grammar requirements, not prompt requests."""
from __future__ import annotations

import asyncio
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from pipeline.models.rule_response_schema import (
    RULE_NL_MAX_LENGTH,
    batch_rules_response_model,
    normalize_batch_rules_response,
    normalize_rules_response,
    rules_response_model,
)
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, ControlSubtreeNode
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.steps.step7_analyze_code.batch_analyze import _collect_keyed_concern
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    analyze_code_three_split,
)
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore


def _rule(*, text: str = "") -> dict:
    return {"nl": text}


def _effect(op: str, *, target=None, value=None, from_source=None) -> dict:
    return {
        "op": op, "target": target, "value": value,
        "from_source": from_source,
    }


def _model_rule_payload(text: str) -> dict:
    return {"nl": text}


def _frame_payload(
    response_model,
    *,
    meaning: str,
    stereotype: str,
    responsibility_missing: str = "",
) -> dict:
    slots_type = response_model.model_fields["slots"].annotation
    slots = {}
    for key in slots_type.model_fields:
        is_responsibility = ":FUNCTION:RESPONSIBILITY:" in key
        partial = is_responsibility and bool(responsibility_missing)
        slots[key] = {
            "meaning": meaning,
            "status": "partial" if partial else "sufficient",
            "missing_context": [responsibility_missing] if partial else [],
        }
    return {"slots": slots, "stereotype": stereotype}


def _rule_frame_payload(response_model, *, meaning: str) -> dict:
    slots_type = response_model.model_fields["slots"].annotation
    return {"slots": {
        key: {
            "meaning": meaning,
            "status": "sufficient",
            "missing_context": [],
        }
        for key in slots_type.model_fields
    }}


def _table_frame_payload(response_model, *, meaning: str, role: str) -> dict:
    slots_type = response_model.model_fields["slots"].annotation
    roles_type = response_model.model_fields["table_roles"].annotation
    return {
        "slots": {
            key: {
                "meaning": meaning,
                "status": "sufficient",
                "missing_context": [],
            }
            for key in slots_type.model_fields
        },
        "table_roles": {key: role for key in roles_type.model_fields},
    }


def _return_inventory(*lines: int, lang: str = "c") -> BranchInventory:
    last = max(lines, default=1)
    source_lines = [""] * last
    termination = []
    for line in lines:
        source_lines[line - 1] = f"return {line};"
        termination.append((line, "exit", f"return {line};"))
    return BranchInventory(
        lang=lang,
        roots=(),
        all_branches=(),
        callees=frozenset(),
        source_values=frozenset(),
        active_lines=frozenset(lines),
        termination_rows=tuple(termination),
        return_expressions=frozenset(str(line) for line in lines),
        source_lines=tuple(source_lines),
    )


class FrameworkAssignedSchemaTest(unittest.TestCase):
    def test_rule_narrative_has_room_for_complete_sentence_but_keeps_a_bound(self):
        model = rules_response_model([5])
        model.model_validate({"rules": {"R5": _rule(text="가" * 301)}})
        with self.assertRaises(ValidationError):
            model.model_validate({
                "rules": {"R5": _rule(text="가" * (RULE_NL_MAX_LENGTH + 1))},
            })

    def test_required_keys_model_owned_leaves_and_canonical_conversion(self):
        model = rules_response_model([5, 9])
        parsed = model.model_validate({
            "rules": {
                "R5": _rule(text="5를 반환한다"),
                "R9": _rule(text="9를 반환한다"),
            },
        })
        canonical = normalize_rules_response(
            parsed,
            [5, 9],
            {
                "conditions": {},
                "rows": {
                    "R5": {"when": [], "direct": ["E1"]},
                    "R9": {"when": [], "direct": ["E2"]},
                },
                "effects": {
                    "E1": _effect("ret", value="5", from_source=True),
                    "E2": _effect("ret", value="9", from_source=True),
                },
            },
        )
        self.assertEqual([5, 9], [rule.anchor_line for rule in canonical.rules])
        self.assertEqual(
            [['ret(F("5"))'], ['ret(F("9"))']],
            [rule.then for rule in canonical.rules],
        )

        invalid = (
            {"rules": {"R5": _rule()}},
            {"rules": {
                "R5": _rule(),
                "R9": _rule(),
                "R10": _rule(),
            }},
            {"rules": {
                "R5": {"then": ["E1"], "nl": ""},
                "R9": _rule(),
            }},
            {"rules": {
                "R5": {},
                "R9": _rule(),
            }},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                model.model_validate(payload)

    def test_invalid_parser_effect_positions_quarantine_only_that_row(self):
        model = rules_response_model([5, 9])
        parsed = model.model_validate({
            "rules": {"R5": _rule(), "R9": _rule()},
        })

        canonical = normalize_rules_response(parsed, [5, 9], {
            "conditions": {},
            "rows": {
                "R5": {"when": [], "direct": ["E1"]},
                "R9": {"when": [], "direct": ["E2"]},
            },
            "effects": {
                "E1": _effect("select", target="db:orders", value="unexpected"),
                "E2": _effect("select", target="db:orders"),
            },
        })

        self.assertTrue(canonical.rules[0].then[0].startswith("invalid_effect("))
        self.assertEqual(['select("db:orders")'], canonical.rules[1].then)

    def test_compact_direct_effect_ref_round_trips_without_model_repetition(self):
        model = rules_response_model([5])
        parsed = model.model_validate({
            "rules": {"R5": _rule(text="오류를 반환한다")},
        })
        canonical = normalize_rules_response(
            parsed,
            [5],
            {
                "conditions": {"C1": '(status == "A")'},
                "rows": {"R5": {"when": ["!C1"], "direct": ["E1"]}},
                "effects": {
                    "E1": _effect("ret", value="RC_ERR", from_source=True),
                },
            },
        )

        self.assertEqual('not (F("(status == \\"A\\")"))', canonical.rules[0].cond)
        self.assertEqual(['ret(F("RC_ERR"))'], canonical.rules[0].then)

    def test_continuation_coordinate_is_materialized_without_model_rewrite(self):
        model = rules_response_model([12])
        parsed = model.model_validate({
            "rules": {"R12": _rule(text="앞선 분기 뒤 감사 로그를 남긴다")},
        })
        canonical = normalize_rules_response(parsed, [12], {
            "conditions": {"C3": "flag"},
            "rows": {"R12": {
                "when": ["C3"],
                "after_control": "IF@8",
                "direct": ["E1"],
            }},
            "effects": {"E1": _effect("call", target="audit")},
        })
        self.assertEqual(
            'F("flag") and after_control("IF@8")',
            canonical.rules[0].cond,
        )
        self.assertEqual(['call("audit")'], canonical.rules[0].then)

    def test_private_reference_leak_is_replaced_with_grounded_evidence(self):
        model = rules_response_model([538])
        parsed = model.model_validate({
            "rules": {"R538": _rule(
                text="조건 C471과 C527이 모두 성립하지 않을 때 E1과 E2를 수행한다",
            )},
        })
        canonical = normalize_rules_response(parsed, [538], {
            "conditions": {
                "C471": "pay_status == PAID",
                "C527": "audit_disabled == 1",
            },
            "rows": {"R538": {
                "when": ["!C471", "!C527"], "direct": ["E1", "E2"],
            }},
            "effects": {
                "E1": _effect("call", target="audit_log"),
                "E2": _effect("call", target="debug_log"),
            },
        })
        narrative = canonical.rules[0].nl
        self.assertNotIn("C471", narrative)
        self.assertNotIn("C527", narrative)
        self.assertNotIn("E1", narrative)
        self.assertNotIn("E2", narrative)
        self.assertIn("pay_status == PAID", narrative)
        self.assertIn('call("audit_log")', narrative)

    def test_ambiguous_single_business_code_is_not_rewritten(self):
        model = rules_response_model([5])
        parsed = model.model_validate({
            "rules": {"R5": _rule(text="상태 C3이면 주문을 확인한다")},
        })
        canonical = normalize_rules_response(parsed, [5], {
            "conditions": {"C3": "status == C3"},
            "rows": {"R5": {"when": ["C3"], "direct": []}},
            "effects": {},
        })
        self.assertEqual("상태 C3이면 주문을 확인한다", canonical.rules[0].nl)


class DbmsAssignedSchemaTest(unittest.TestCase):
    def test_node_and_row_keys_are_both_required(self):
        assignments = {"1-2": [1, 2], "5-5": []}
        model = batch_rules_response_model(assignments)
        parsed = model.model_validate({
            "nodes": {
                "1-2": {
                    "rules": {
                        "R1": _rule(),
                        "R2": _rule(),
                    },
                },
                "5-5": {"rules": {}},
            },
        })
        canonical = normalize_batch_rules_response(parsed, assignments, {
            "1-2": {
                "conditions": {}, "effects": {},
                "rows": {"R1": {"when": []}, "R2": {"when": []}},
            },
            "5-5": {"conditions": {}, "effects": {}, "rows": {}},
        })
        self.assertEqual([[1, 2], []], [
            [rule.anchor_line for rule in node.rules] for node in canonical.nodes
        ])

        with self.assertRaises(ValidationError):
            model.model_validate({
                "nodes": {
                    "1-2": {
                        "rules": {
                            "R1": _rule(),
                            "R2": _rule(),
                        },
                    },
                },
            })


class WiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_framework_runner_receives_and_normalizes_assigned_schema(self):
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
                reads=["db:orders"], direct_reads=["db:orders"],
                extra={"control_subtree": [ControlSubtreeNode(
                    node_id="if", ntype="IF", start=1, end=4, expression='flag == "1"',
                )], "table_op": {"db:orders": "SELECT"},
                    "data_object_usages": (DataObjectUsageEvidence(
                        source_id="fn", raw_reference="orders", name="orders",
                        access="READ", evidence_file=str(source), evidence_line=1,
                        table_id="db:orders", resolution="exact",
                    ),)},
            )
            context.extra.setdefault("ast_statements", ())
            slots = PrioritySemaphore(1)

            async def fake_rule(*, rule_name, response_model, **_kwargs):
                self.assertEqual(0, slots.free, "framework LLM call must own one run slot")
                if rule_name == "code_summary":
                    return response_model.model_validate(_frame_payload(
                        response_model,
                        meaning="flag가 1이면 주문을 조회하고 처리 결과를 반환한다.",
                        stereotype="Validator",
                        responsibility_missing="flag 1의 업무 의미는 무엇인가?",
                    ))
                if rule_name == "code_table_insights":
                    return response_model.model_validate(_table_frame_payload(
                        response_model,
                        meaning="조건 판단에 필요한 주문을 조회한다.",
                        role="lookup",
                    ))
                return response_model.model_validate(_rule_frame_payload(
                    response_model, meaning="flag가 1이면 처리한다",
                ))

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=fake_rule,
            ):
                await analyze_code_three_split(
                    "fn", {"fn": context}, {"fn": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                    llm_slots=slots,
                    table_descriptions={
                        "db:orders": {"description": "주문", "columns": ["status"]},
                    },
                )
        self.assertEqual([1], [rule["anchor_line"] for rule in node.extra["rules"]])
        # parser AST 조건은 괄호 래퍼 없이 온다 (spec 016).
        self.assertEqual('F("flag == \\"1\\"")', node.extra["rules"][0]["cond"])
        self.assertEqual(
            "flag 1의 업무 의미는 무엇인가?",
            node.extra["questions"][0]["text"],
        )
        self.assertEqual([{
            "table": "db:orders",
            "role": "lookup",
            "usage_summary": "조건 판단에 필요한 주문을 조회한다.",
            "columns": [],
        }], node.extra["table_insights"])

    async def test_framework_independent_concerns_start_together_after_global_warmup(self):
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
                reads=["db:orders"], direct_reads=["db:orders"],
                extra={
                    "control_subtree": [ControlSubtreeNode(
                        node_id="if", ntype="IF", start=1, end=4, expression='flag == "1"',
                    )],
                    "table_op": {"db:orders": "SELECT"},
                    "data_object_usages": (DataObjectUsageEvidence(
                        source_id="fn", raw_reference="orders", name="orders",
                        access="READ", evidence_file=str(source), evidence_line=1,
                        table_id="db:orders", resolution="exact",
                    ),),
                },
            )
            context.extra.setdefault("ast_statements", ())
            started: set[str] = set()
            all_started = asyncio.Event()

            async def fake_rule(*, rule_name, response_model, user_values, **_kwargs):
                started.add(rule_name)
                if len(started) == 3:
                    all_started.set()
                await asyncio.wait_for(all_started.wait(), timeout=0.5)
                if rule_name == "code_summary":
                    return response_model.model_validate(_frame_payload(
                        response_model,
                        meaning="flag가 1이면 주문을 조회하고 처리 결과를 반환한다.",
                        stereotype="Validator",
                    ))
                if rule_name == "code_table_insights":
                    return response_model.model_validate(_table_frame_payload(
                        response_model, meaning="주문을 조회한다.", role="lookup",
                    ))
                return response_model.model_validate(_rule_frame_payload(
                    response_model, meaning="flag가 1이면 처리한다",
                ))

            with patch(
                "pipeline.steps.step7_analyze_code.three_split_llm_fanout.run_yaml_rule",
                side_effect=fake_rule,
            ):
                await analyze_code_three_split(
                    "fn", {"fn": context}, {"fn": node},
                    SimpleNamespace(name="framework", prompts_dir="."),
                    source_cache=SourceFileCache(),
                    llm_slots=PrioritySemaphore(3),
                )
        self.assertEqual(
            {"code_summary", "code_rules_examples", "code_table_insights"}, started,
        )

    async def test_dbms_collector_builds_fixed_schema_before_canonical_partition(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.sql"
            source.write_text("IF flag = 1 THEN\n  NULL;\nEND IF;\n", encoding="utf-8")
            # spec 117 — DBMS rule 대상은 루틴의 하위 노드이고, 분기표는 **소유 루틴**이
            # 만든다. 배분된 행(ctx.extra["rule_rows"])이 스키마의 required 키가 된다.
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
                extra={"control_subtree": [ControlSubtreeNode(
                    node_id="if", ntype="IF", start=1, end=3, expression='flag == "1"',
                )]},
            )
            routine_context.extra.setdefault("ast_statements", ())
            context = AnalysisContext(
                id="a", signature="if", file_path=str(source),
                start_line=1, end_line=3,
                extra={"rule_rows": (1,)},
            )
            context.extra.setdefault("ast_statements", ())
            slots = PrioritySemaphore(1)

            async def fake_rule(*, response_model, **_kwargs):
                self.assertEqual(0, slots.free, "DBMS LLM call must own one run slot")
                item_type = response_model.model_fields["nodes"].annotation.model_fields[
                    "1-3"
                ].annotation
                return response_model.model_validate({
                    "nodes": {"1-3": _rule_frame_payload(
                        item_type, meaning="flag가 1인 경로다",
                    )},
                })

            with (
                patch(
                    "pipeline.steps.step7_analyze_code.batch_analyze.run_yaml_rule",
                    side_effect=fake_rule,
                ),
            ):
                result = await _collect_keyed_concern(
                    parent_id="parent",
                    node_ids=["a"],
                    concern="rules",
                    rule_name="code_rules_examples_batch",
                    response_model=object,
                    prompts_dir=Path("."),
                    contexts={"a": context, "p": routine_context},
                    code_by_id={"a": node, "p": routine},
                    cost_budget=None,
                    codebase_notes="",
                    module_intents={},
                    table_descriptions={},
                    source_cache=SourceFileCache(),
                    llm_slots=slots,
                    strategy_name="dbms",
                )
        self.assertEqual([1], [rule.anchor_line for rule in result["a"].rules])


if __name__ == "__main__":
    unittest.main()
