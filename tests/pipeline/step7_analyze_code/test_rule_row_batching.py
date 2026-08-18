# -*- coding: utf-8 -*-
"""규칙 행 배칭은 content 재요청 없이 trusted 행과 품질 finding을 분리한다."""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from llm.runners.model_output_error import ModelOutputError

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.models.code_analysis_output import RulesExamplesOutput, RuleItem
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import (
    expected_rule_condition,
    rule_evidence,
)
from pipeline.steps.step7_analyze_code.rule_row_batching import (
    DEFAULT_ROWS_PER_CALL,
    MAX_RULE_EVIDENCE_CHARS,
    MAX_RULE_EVIDENCE_REFERENCES,
    MAX_RULE_SEMANTIC_SLOTS,
    SUMMARY_WAKE_PRIORITY,
    collect_rules_by_rows,
    plan_batches,
    rule_queue_priority,
)

SOURCE = (
    'if (a == "1")\n{\n  x();\n}\n'
    'else if (a == "2")\n{\n  y();\n}\n'
    "else\n{\n  z();\n}\n"
)
IF2 = ControlSubtreeNode(
    node_id="if2", ntype="IF", start=5, end=12, expression='a == "2"',
    children=[ControlSubtreeNode(node_id="el2", ntype="ELSE", start=9, end=12)],
)
TREE = [ControlSubtreeNode(
    node_id="if1", ntype="IF", start=1, end=12, expression='a == "1"',
    children=[ControlSubtreeNode(
        node_id="el1", ntype="ELSE", start=5, end=12, children=[IF2],
    )],
)]


def _inventory():
    # parser AST 계약 (spec 016): 조건은 expression, 호출은 call_lines 로 온다.
    return build_inventory(
        SOURCE, TREE, "c", statements=(),
        call_lines=((3, "x"), (7, "y"), (11, "z")),
    )


def _good(anchor):
    calls = {1: 'call("x")', 5: 'call("y")', 9: 'call("z")'}
    return RuleItem(
        anchor_line=anchor,
        cond=expected_rule_condition(_inventory(), anchor),
        then=[calls[anchor]],
    )


class BatchingContract(unittest.TestCase):
    def test_default_row_count_is_only_a_hard_guard_after_cost_based_packing(self):
        self.assertEqual(24, DEFAULT_ROWS_PER_CALL)
        self.assertEqual(14, MAX_RULE_SEMANTIC_SLOTS)
        self.assertEqual(16_000, MAX_RULE_EVIDENCE_CHARS)
        self.assertEqual(64, MAX_RULE_EVIDENCE_REFERENCES)

    def test_many_direct_refs_are_input_cost_not_obsolete_output_shape(self):
        class DirectHeavyInventory:
            lang = "c"

            @staticmethod
            def table_rows():
                # Product table rows do not contain derived ``when``/``direct``.
                return [
                    {"line": line, "role": "entry"}
                    for line in range(1, 8)
                ]

            @staticmethod
            def at_line(_line):
                # 이 대역에는 분기 노드가 없다 — 소비자 계약(spec 118 대안 절 보정)이
                # 소유 branch 를 묻기 때문에 "없음"을 명시한다.
                return None

            @staticmethod
            def direct_evidence(line):
                return "", frozenset(), tuple(
                    (f"row{line}_target{index}", str(index))
                    for index in range(10)
                )

            @staticmethod
            def direct_callees(_line):
                return set()

            @staticmethod
            def direct_statement_effects(_line):
                return ()

            @staticmethod
            def has_nested_control(_line):
                return False

        inventory = DirectHeavyInventory()

        batches = plan_batches(inventory)

        self.assertEqual([[1, 2, 3, 4, 5, 6], [7]], batches)

    def test_cost_bounds_reject_invalid_configuration(self):
        with self.assertRaises(ValueError):
            plan_batches(_inventory(), max_semantic_slots=0)
        with self.assertRaises(ValueError):
            plan_batches(_inventory(), max_evidence_chars=0)
        with self.assertRaises(ValueError):
            plan_batches(_inventory(), max_evidence_references=0)

    def test_cheap_rows_pack_beyond_obsolete_eight_row_target(self):
        class CheapInventory:
            lang = "c"

            @staticmethod
            def table_rows():
                return [
                    {"line": line, "role": "entry", "end_line": line}
                    for line in range(1, 13)
                ]

            @staticmethod
            def at_line(_line):
                return None

            @staticmethod
            def direct_evidence(line):
                return f"x{line}();", frozenset(), ((f"x{line}", ""),)

            @staticmethod
            def direct_callees(_line):
                return set()

            @staticmethod
            def direct_statement_effects(_line):
                return ()

            @staticmethod
            def has_nested_control(_line):
                return False

        self.assertEqual(
            [list(range(1, 13))],
            plan_batches(
                CheapInventory(),
                semantic_slot_count=lambda lines: len(lines),
            ),
        )

    def test_slot_heavy_rows_split_even_below_row_guard(self):
        self.assertEqual(
            [[1], [5], [9]],
            plan_batches(
                _inventory(),
                max_semantic_slots=3,
                semantic_slot_count=lambda lines: 2 * len(lines),
            ),
        )

    def test_evidence_heavy_rows_split_by_exact_compact_packet_size(self):
        single_chars = len(json.dumps(
            rule_evidence(_inventory(), [1]),
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        self.assertEqual(
            [[1], [5], [9]],
            plan_batches(
                _inventory(),
                max_evidence_chars=single_chars,
            ),
        )

    def test_rule_lpt_priority_never_overtakes_summary(self):
        small = rule_queue_priority(100)
        large = rule_queue_priority(1_000)
        self.assertLess(large, small)
        self.assertLess(SUMMARY_WAKE_PRIORITY, large)
        with self.assertRaises(ValueError):
            rule_queue_priority(-1)

    def test_nested_branch_rows_own_only_direct_callees(self):
        rows = {row["line"]: row for row in _inventory().table_rows()}
        self.assertEqual(["x"], rows[1]["callees"])
        self.assertEqual(["y"], rows[5]["callees"])
        self.assertEqual(["z"], rows[9]["callees"])

    def test_prompt_evidence_names_direct_facts_once_and_rows_use_refs(self):
        evidence = rule_evidence(_inventory(), [1, 5, 9])

        self.assertEqual(["C1"], evidence["rows"]["R1"]["when"])
        self.assertEqual(["!C1", "C5"], evidence["rows"]["R5"]["when"])
        self.assertEqual(["!C1", "!C5"], evidence["rows"]["R9"]["when"])
        self.assertEqual(["E1"], evidence["rows"]["R1"]["direct"])
        self.assertEqual("x", evidence["effects"]["E1"]["target"])

    def test_prompt_evidence_deduplicates_repeated_direct_assignment_refs(self):
        class RepeatedAssignmentInventory:
            lang = "c"

            @staticmethod
            def table_rows():
                return [{"line": 1, "role": "entry"}]

            @staticmethod
            def at_line(_line):
                return None      # 분기 없음 — spec 118 대안 절 보정의 소비자 계약

            @staticmethod
            def direct_evidence(_line):
                return "x = 1; x = 1;", set(), [("x", "1"), ("x", "1")]

            @staticmethod
            def direct_callees(_line):
                return set()

            @staticmethod
            def direct_statement_effects(_line):
                return ()

            @staticmethod
            def has_nested_control(_line):
                return False

        evidence = rule_evidence(RepeatedAssignmentInventory(), [1])
        self.assertEqual(["E1"], evidence["rows"]["R1"]["direct"])
        self.assertEqual(1, len(evidence["effects"]))

    def test_post_test_loop_condition_is_not_a_body_precondition(self):
        source = "do {\n  x = 1;\n  if (flag) { y = 2; }\n} while (again);\n"
        tree = [ControlSubtreeNode(
            node_id="do", ntype="LOOP", start=1, end=4, expression="again",
            condition_timing="post",
            children=[ControlSubtreeNode(
                node_id="if", ntype="IF", start=3, end=3, expression="flag",
            )],
        )]
        inventory = build_inventory(
            source, tree, "c",
            statements=(
                StatementFact("s2", "ASSIGNMENT", 2, 2, expression="1", target="x", operator="="),
                StatementFact("s3", "ASSIGNMENT", 3, 3, expression="2", target="y", operator="="),
            ),
        )

        evidence = rule_evidence(inventory, [1, 3])

        self.assertEqual([], evidence["rows"]["R1"]["when"])
        self.assertEqual("again", evidence["rows"]["R1"]["post_condition"])
        self.assertEqual(["C3"], evidence["rows"]["R3"]["when"])
        self.assertEqual("flag", evidence["conditions"]["C3"])

    def test_plan_batches_covers_obligation_rows_only(self):
        self.assertEqual([[1], [5, 9]], plan_batches(_inventory(), rows_per_call=2))

    def test_small_parent_and_child_subtree_are_not_split_at_batch_boundary(self):
        source = "\n".join([
            "a = 0;", "b = 0;", "c = 0;", "d = 0;", "e = 0;", "f = 0;", "g = 0;",
            "if (start > 0) {", "  for (i = 0; i < n; i++) { x = i; }", "}",
        ])
        tree = [ControlSubtreeNode(
            node_id="if", ntype="IF", start=8, end=10, expression="start > 0",
            children=[ControlSubtreeNode(
                node_id="loop", ntype="LOOP", start=9, end=9, expression="i < n",
            )],
        )]
        statements = tuple(
            StatementFact(f"s{line}", "ASSIGNMENT", line, line,
                          expression="0", target=chr(96 + line), operator="=")
            for line in range(1, 8)
        ) + (
            StatementFact("s9", "ASSIGNMENT", 9, 9,
                          expression="i", target="x", operator="="),
        )
        inventory = build_inventory(source, tree, "c", statements=statements)

        batches = plan_batches(inventory, rows_per_call=2)

        self.assertIn([8, 9], batches)

    def test_valid_batches_are_collected_once(self):
        seen = []

        async def invoke(rows):
            seen.append(tuple(rows))
            return RulesExamplesOutput(rules=[_good(line) for line in rows])

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE, rows_per_call=2,
        ))
        self.assertEqual([(1,), (5, 9)], sorted(seen))
        self.assertEqual([1, 5, 9], [rule.anchor_line for rule in collection.rules])
        self.assertEqual([], collection.findings)
        self.assertEqual(2, collection.calls)

    def test_content_rejection_is_quarantined_after_one_call_with_row_reason(self):
        calls = 0

        async def invoke(rows):
            nonlocal calls
            calls += 1
            return RulesExamplesOutput(rules=[
                RuleItem(anchor_line=line, cond='F("a") == "99"', then=[])
                for line in rows
            ])

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE, rows_per_call=10,
        ))
        self.assertEqual(1, calls)
        self.assertEqual([], collection.rules)
        self.assertEqual({"rejected"}, {finding.status for finding in collection.findings})
        self.assertEqual(
            {"fn::R-1", "fn::R-5", "fn::R-9"},
            {finding.target_id for finding in collection.findings},
        )
        self.assertTrue(
            any("창작" in reason for finding in collection.findings for reason in finding.reasons)
        )

    def test_partial_semantic_slot_is_visible_and_not_published_as_trusted_rule(self):
        fact = SimpleNamespace(kind="RULE", attributes={"anchor_line": 1})
        slot = SimpleNamespace(
            slot_id="SL:test:DECISION:TRUE_OUTCOME:1",
            family="DECISION",
            role="TRUE_OUTCOME",
            evidence_refs=("rule",),
            meaning="일부 결과만 확인됨",
            status="partial",
            missing_context=("직접 효과",),
        )
        frame = SimpleNamespace(
            profile=SimpleNamespace(evidence={"rule": fact}),
            slots={slot.slot_id: slot},
        )

        async def invoke(rows):
            return RulesExamplesOutput.model_construct(
                rules=[_good(line) for line in rows],
                semantic_frame=frame if 1 in rows else None,
            )

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE,
            rows_per_call=1,
        ))

        self.assertNotIn(1, [rule.anchor_line for rule in collection.rules])
        finding = next(
            item for item in collection.findings if item.coordinates == (1,)
        )
        self.assertEqual("unverified", finding.status)
        self.assertIn("직접 효과", finding.reasons[0])

    def test_missing_row_is_unavailable_while_valid_sibling_survives(self):
        async def invoke(rows):
            return RulesExamplesOutput(rules=[_good(rows[0])])

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE, rows_per_call=10,
        ))
        self.assertEqual([1], [rule.anchor_line for rule in collection.rules])
        unavailable = {
            finding.coordinates[0]
            for finding in collection.findings if finding.status == "unavailable"
        }
        self.assertEqual({5, 9}, unavailable)

    def test_one_bad_leaf_is_quarantined_without_closing_the_batch(self):
        calls = 0

        async def invoke(rows):
            nonlocal calls
            calls += 1
            bad = RuleItem.model_construct(
                anchor_line=rows[0], cond="a\nb", then=[], nl="bad",
            )
            good = [_good(line) for line in rows[1:]]
            return RulesExamplesOutput.model_construct(
                rules=[bad, *good],
            )

        result = asyncio.run(collect_rules_by_rows(
            invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE,
        ))
        self.assertEqual(1, calls)
        self.assertEqual([5, 9], [rule.anchor_line for rule in result.rules])
        self.assertEqual(
            [(1,)],
            [finding.coordinates for finding in result.findings
             if finding.status == "rejected"],
        )

    def test_runner_exception_propagates_without_second_layer_retry(self):
        calls = 0

        async def invoke(rows):
            nonlocal calls
            calls += 1
            raise RuntimeError("transport attempts exhausted")

        with self.assertRaisesRegex(RuntimeError, "transport attempts exhausted"):
            asyncio.run(collect_rules_by_rows(
                invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE,
            ))
        self.assertEqual(1, calls)

    def test_model_output_failure_marks_batch_unavailable_and_other_batch_continues(self):
        calls = []

        async def invoke(rows):
            calls.append(tuple(rows))
            if 1 in rows:
                raise ModelOutputError(
                    rule_name="code_rules_examples",
                    kind="repetition",
                    detail="loop",
                    original_type="RepetitionLoopAborted",
                )
            return RulesExamplesOutput(rules=[_good(line) for line in rows])

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE, rows_per_call=1,
        ))
        self.assertEqual([(1,), (5,), (9,)], sorted(calls))
        self.assertEqual([5, 9], [rule.anchor_line for rule in collection.rules])
        self.assertEqual(
            [(1,)],
            [finding.coordinates for finding in collection.findings
             if finding.status == "unavailable"],
        )

    def test_deadline_bisects_required_rows_until_all_meaning_is_recovered(self):
        calls = []

        async def invoke(rows):
            calls.append(tuple(rows))
            if len(rows) > 1:
                raise ModelOutputError(
                    rule_name="code_rules_examples",
                    kind="deadline",
                    detail="test deadline",
                    original_type="ModelGenerationDeadlineExceeded",
                )
            return RulesExamplesOutput(rules=[_good(line) for line in rows])

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke,
            inventory=_inventory(),
            target_scope="fn",
            source=SOURCE,
            rows_per_call=3,
        ))

        self.assertEqual([1, 5, 9], [rule.anchor_line for rule in collection.rules])
        self.assertFalse([
            finding for finding in collection.findings
            if finding.status == "unavailable"
        ])
        self.assertEqual(5, collection.calls)
        self.assertEqual(
            {(1, 5, 9), (1,), (5, 9), (5,), (9,)},
            set(calls),
        )

    def test_repetition_bisects_rows_and_keeps_bad_singleton_visible(self):
        calls = []

        async def invoke(rows):
            calls.append(tuple(rows))
            if len(rows) > 1 or list(rows) == [5]:
                raise ModelOutputError(
                    rule_name="code_rules_examples",
                    kind="repetition",
                    detail="test repetition",
                    original_type="RepetitionLoopAborted",
                )
            return RulesExamplesOutput(rules=[_good(line) for line in rows])

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke,
            inventory=_inventory(),
            target_scope="fn",
            source=SOURCE,
            rows_per_call=3,
        ))

        self.assertEqual([1, 9], [rule.anchor_line for rule in collection.rules])
        self.assertEqual(
            [(5,)],
            [finding.coordinates for finding in collection.findings
             if finding.status == "unavailable"],
        )
        self.assertEqual(5, collection.calls)
        self.assertEqual(
            {(1, 5, 9), (1,), (5, 9), (5,), (9,)},
            set(calls),
        )

    def test_batches_run_concurrently(self):
        first_started = asyncio.Event()
        inflight = 0
        peak = 0

        async def invoke(rows):
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            if not first_started.is_set():
                first_started.set()
                await asyncio.sleep(0.05)
            inflight -= 1
            return RulesExamplesOutput(rules=[_good(line) for line in rows])

        collection = asyncio.run(collect_rules_by_rows(
            invoke=invoke, inventory=_inventory(), target_scope="fn", source=SOURCE, rows_per_call=1,
        ))
        self.assertGreaterEqual(peak, 2)
        self.assertEqual([1, 5, 9], [rule.anchor_line for rule in collection.rules])


if __name__ == "__main__":
    unittest.main()
