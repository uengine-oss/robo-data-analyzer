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
    batch_rules_response_model,
    normalize_batch_rules_response,
    normalize_rules_response,
    rules_response_model,
)
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, ControlSubtreeNode
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.steps.step7_analyze_code.batch_analyze import _collect_keyed_concern
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    analyze_code_three_split,
)
from shared.files.cache import SourceFileCache
from shared.concurrency.priority_semaphore import PrioritySemaphore


def _rule(*, cond=None, then=None, extra=None, text: str = "") -> dict:
    return {
        "cond": list(cond or ()),
        "then": list(then or ()),
        "extra": list(extra or ()),
        "nl": text,
    }


def _effect(op: str, *, target=None, value=None, from_source=None) -> dict:
    return {
        "op": op, "target": target, "value": value,
        "from_source": from_source,
    }


def _model_rule_payload(text: str) -> dict:
    return {
        "cond": ["C1"],
        "then": [],
        "extra": [],
        "nl": text,
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
    def test_required_keys_model_owned_leaves_and_canonical_conversion(self):
        model = rules_response_model([5, 9])
        parsed = model.model_validate({
            "rules": {
                "R5": _rule(then=["E1"]),
                "R9": _rule(then=["E2"]),
            },
        })
        canonical = normalize_rules_response(
            parsed,
            [5, 9],
            effect_sources={
                "E1": _effect("ret", value="5", from_source=True),
                "E2": _effect("ret", value="9", from_source=True),
            },
        )
        self.assertEqual([5, 9], [rule.anchor_line for rule in canonical.rules])
        self.assertEqual(
            [['ret(F("5"))'], ['ret(F("9"))']],
            [rule.then for rule in canonical.rules],
        )

        invalid = (
            {"rules": {"R5": _rule(then=["E1"])}},
            {"rules": {
                "R5": _rule(then=["E1"]),
                "R9": _rule(then=["E2"]),
                "R10": _rule(),
            }},
            {"rules": {
                "R5": {"then": "E1", "nl": ""},
                "R9": _rule(then=["E2"]),
            }},
            {"rules": {
                "R5": {key: value for key, value in _rule(then=["E1"]).items()
                       if key != "extra"},
                "R9": _rule(then=["E2"]),
            }},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                model.model_validate(payload)

    def test_invalid_effect_positions_quarantine_only_that_row(self):
        model = rules_response_model([5, 9])
        parsed = model.model_validate({
            "rules": {
                "R5": _rule(extra=[
                    _effect("select", target="db:orders", value="unexpected"),
                ]),
                "R9": _rule(extra=[
                    _effect("select", target="db:orders"),
                ]),
            },
        })

        canonical = normalize_rules_response(parsed, [5, 9])

        self.assertTrue(canonical.rules[0].then[0].startswith("invalid_effect("))
        self.assertEqual(['select("db:orders")'], canonical.rules[1].then)

    def test_compact_direct_effect_ref_round_trips_without_model_repetition(self):
        model = rules_response_model([5])
        parsed = model.model_validate({
            "rules": {"R5": _rule(cond=["!C1"], then=["E1"])},
        })
        canonical = normalize_rules_response(
            parsed,
            [5],
            {"C1": '(status == "A")'},
            {"E1": _effect("ret", value="RC_ERR", from_source=True)},
        )

        self.assertEqual('not (F("(status == \\"A\\")"))', canonical.rules[0].cond)
        self.assertEqual(['ret(F("RC_ERR"))'], canonical.rules[0].then)


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
        canonical = normalize_batch_rules_response(parsed, assignments)
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
                reads=["db:orders"],
                extra={"control_subtree": [ControlSubtreeNode(
                    node_id="if", ntype="IF", start=1, end=4, expression='flag == "1"',
                )], "table_op": {"db:orders": "SELECT"}},
            )
            context.extra.setdefault("ast_statements", ())
            slots = PrioritySemaphore(1)

            async def fake_rule(*, rule_name, response_model, **_kwargs):
                self.assertEqual(0, slots.free, "framework LLM call must own one run slot")
                if rule_name == "code_summary":
                    return response_model.model_validate({
                        "summary": "flag가 1이면 주문을 조회하고 처리 결과를 반환한다.",
                        "stereotype": "Validator",
                        "questions": [{
                            "text": "flag 1의 업무 의미는 무엇인가?",
                            "reason": "제공 근거에 상태 정의가 없다",
                        }],
                    })
                if rule_name == "code_table_insights":
                    return response_model.model_validate({
                        "table_insights": {
                            "db:orders": {
                                "role": "lookup",
                                "usage_summary": "조건 판단에 필요한 주문을 조회한다.",
                            }
                        }
                    })
                return response_model.model_validate({
                    "rules": {"R1": {
                        **_model_rule_payload("flag가 1이면 처리한다"),
                    }},
                })

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
                start_line=1, end_line=4, reads=["db:orders"],
                extra={
                    "control_subtree": [ControlSubtreeNode(
                        node_id="if", ntype="IF", start=1, end=4, expression='flag == "1"',
                    )],
                    "table_op": {"db:orders": "SELECT"},
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
                    return response_model.model_validate({
                        "summary": "flag가 1이면 주문을 조회하고 처리 결과를 반환한다.",
                        "stereotype": "Validator",
                        "questions": [],
                    })
                if rule_name == "code_table_insights":
                    return response_model.model_validate({
                        "table_insights": {"db:orders": {
                            "role": "lookup",
                            "usage_summary": "주문을 조회한다.",
                        }},
                    })
                return response_model.model_validate({
                    "rules": {"R1": {
                        **_model_rule_payload("flag가 1이면 처리한다"),
                    }},
                })

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
            node = CodeNode(
                id="a", name="if", signature="if", file_path=str(source),
                start_line=1, end_line=3, label="IF",
            )
            context = AnalysisContext(
                id="a", signature="if", file_path=str(source),
                start_line=1, end_line=3,
                extra={"control_subtree": [ControlSubtreeNode(
                    node_id="if", ntype="IF", start=1, end=3, expression='flag == "1"',
                )]},
            )
            context.extra.setdefault("ast_statements", ())
            slots = PrioritySemaphore(1)

            async def fake_rule(*, response_model, **_kwargs):
                self.assertEqual(0, slots.free, "DBMS LLM call must own one run slot")
                return response_model.model_validate({
                    "nodes": {"1-3": {
                        "rules": {"R1": {
                            **_model_rule_payload("flag가 1인 경로다"),
                        }},
                    }},
                })

            with (
                patch(
                    "pipeline.steps.step7_analyze_code.batch_analyze._build_batch_values",
                    return_value=({"unit_id": "unit"}, {}),
                ),
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
                    contexts={"a": context},
                    code_by_id={"a": node},
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
