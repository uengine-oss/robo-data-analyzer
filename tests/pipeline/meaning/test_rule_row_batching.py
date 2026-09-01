# -*- coding: utf-8 -*-
"""Rule obligations are materialized once from parser-owned structure."""
from __future__ import annotations

import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.meaning.rule.meaning_frame import (
    RuleMeaningProjection,
    unavailable_rule_meaning_projections,
)
from pipeline.models.rule_materialization import (
    StructuralEffect,
    materialize_rule_items,
    render_structural_effect,
)
from pipeline.checks.branch_inventory import build_inventory
from pipeline.checks.rule_prompt_evidence import rule_evidence
from pipeline.meaning.rule import row_batching as rule_row_batching
from pipeline.checks.rule_obligations import required_rule_rows
from pipeline.meaning.rule.row_batching import (
    RowCollection,
    apply_rule_meaning_projections,
    materialize_and_validate_rules,
)
from tests.pipeline.parser_call_fixtures import parser_call_occurrences


SOURCE = (
    'if (a == "1")\n{\n  x();\n}\n'
    'else if (a == "2")\n{\n  y();\n}\n'
    'else\n{\n  z();\n}\n'
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
    return build_inventory(
        SOURCE, TREE, "c", statements=(),
        call_occurrences=parser_call_occurrences(
            ((3, "x"), (7, "y"), (11, "z"))
        ),
    )


class DeterministicRuleMaterializationTest(unittest.TestCase):
    def test_required_rows_are_materialized_exactly_once_without_model_calls(self):
        inventory = _inventory()

        collection = materialize_and_validate_rules(
            inventory=inventory,
            target_scope="code:sample:fn",
            source=SOURCE,
        )

        self.assertEqual([1, 5, 9], required_rule_rows(inventory))
        self.assertEqual([1, 5, 9], [rule.anchor_line for rule in collection.rules])
        self.assertEqual([], collection.findings)
        self.assertEqual(0, collection.calls)
        self.assertEqual(['call("x", None, [])'], collection.rules[0].structural_effects)
        self.assertEqual(['call("y", None, [])'], collection.rules[1].structural_effects)
        self.assertEqual(['call("z", None, [])'], collection.rules[2].structural_effects)

    def test_structure_only_row_is_preserved_with_empty_outcome(self):
        source = 'if (ready)\n{\n  ;\n}\n'
        inventory = build_inventory(
            source,
            [ControlSubtreeNode("if", "IF", 1, 4, expression="ready")],
            "c", statements=(),
        )

        collection = materialize_and_validate_rules(
            inventory=inventory,
            target_scope="code:sample:empty",
            source=source,
        )

        self.assertEqual(1, len(collection.rules))
        self.assertEqual('F("ready")', collection.rules[0].structural_condition)
        self.assertEqual([], collection.rules[0].structural_effects)
        self.assertEqual(0, collection.calls)

    def test_unverified_row_is_scoped_and_quarantined_while_sibling_is_trusted(self):
        source = "if (bad) { throw err; }\nif (ready) { go(); }\n"
        inventory = build_inventory(
            source,
            (
                ControlSubtreeNode("bad", "IF", 1, 1, expression="bad"),
                ControlSubtreeNode("ready", "IF", 2, 2, expression="ready"),
            ),
            "java",
            statements=(StatementFact("t1", "THROW", 1, 1),),
            call_occurrences=parser_call_occurrences(((2, "go"),)),
        )

        collection = materialize_and_validate_rules(
            inventory=inventory,
            target_scope="code:sample:fn",
            source=source,
        )

        self.assertEqual([2], [rule.anchor_line for rule in collection.rules])
        self.assertEqual(1, len(collection.findings))
        finding = collection.findings[0]
        self.assertEqual("unverified", finding.status)
        self.assertEqual("code:sample:fn::R-1", finding.target_id)
        self.assertEqual((1,), finding.coordinates)

    def test_only_sufficient_business_rows_are_published(self):
        structural = materialize_and_validate_rules(
            inventory=_inventory(),
            target_scope="code:sample:fn",
            source=SOURCE,
        )
        projections = (
            RuleMeaningProjection(
                "R1", 1,
                "첫 조건", ("첫 업무 처리",),
                "business_rule", "sufficient",
            ),
            RuleMeaningProjection(
                "R5", 5, "", (), "implementation_detail", "not_business",
            ),
            RuleMeaningProjection(
                "R9", 9, "", (),
                "business_rule", "partial",
            ),
        )

        result = apply_rule_meaning_projections(
            structural, projections, target_scope="code:sample:fn",
        )

        self.assertEqual([1], [rule.anchor_line for rule in result.rules])
        self.assertEqual(
            [(5, "excluded"), (9, "unverified")],
            [(finding.coordinates[0], finding.status) for finding in result.findings],
        )
        self.assertEqual(
            "implementation_detail",
            result.findings[0].candidate["row_disposition"],
        )

    def test_sufficient_business_row_without_effect_text_is_not_published(self):
        structural = materialize_and_validate_rules(
            inventory=_inventory(),
            target_scope="code:sample:fn",
            source=SOURCE,
        )
        projections = (
            RuleMeaningProjection(
                "R1", 1, "첫 조건", (), "business_rule", "sufficient",
            ),
            RuleMeaningProjection(
                "R5", 5, "", (), "implementation_detail", "not_business",
            ),
            RuleMeaningProjection(
                "R9", 9, "", (), "implementation_detail", "not_business",
            ),
        )

        result = apply_rule_meaning_projections(
            structural, projections, target_scope="code:sample:fn",
        )

        self.assertEqual([], result.rules)
        self.assertEqual("unverified", result.findings[0].status)

    def test_unavailable_meaning_call_publishes_no_structural_rule(self):
        structural = materialize_and_validate_rules(
            inventory=_inventory(),
            target_scope="code:sample:fn",
            source=SOURCE,
        )

        result = apply_rule_meaning_projections(
            structural,
            unavailable_rule_meaning_projections(
                structural.evidence, reason="provider unavailable",
            ),
            target_scope="code:sample:fn",
        )

        self.assertEqual([], result.rules)
        self.assertEqual([1, 5, 9], [item.coordinates[0] for item in result.findings])
        self.assertTrue(all(item.status == "unavailable" for item in result.findings))

    def test_post_test_loop_condition_is_not_a_body_precondition(self):
        source = "do {\n  x = 1;\n  if (flag) { y = 2; }\n} while (again);\n"
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                node_id="do", ntype="LOOP", start=1, end=4,
                expression="again", condition_timing="post",
                children=[ControlSubtreeNode(
                    node_id="if", ntype="IF", start=3, end=3,
                    expression="flag",
                )],
            )],
            "c",
            statements=(
                StatementFact(
                    "s2", "ASSIGNMENT", 2, 2,
                    expression="1", target="x", operator="=",
                ),
                StatementFact(
                    "s3", "ASSIGNMENT", 3, 3,
                    expression="2", target="y", operator="=",
                ),
            ),
        )
        evidence = rule_evidence(inventory, [1, 3])

        rules = materialize_rule_items(evidence, [1, 3])

        self.assertIsNone(rules[0].structural_condition)
        self.assertEqual('F("flag")', rules[1].structural_condition)
        self.assertEqual(['set(F("x"), F("1"))'], rules[0].structural_effects)
        self.assertEqual(['set(F("y"), F("2"))'], rules[1].structural_effects)

    def test_assigned_rows_must_match_evidence_exactly(self):
        evidence = rule_evidence(_inventory(), [1, 5])

        with self.assertRaisesRegex(ValueError, "exactly cover assigned rows"):
            materialize_rule_items(evidence, [1])

    def test_duplicate_or_nonpositive_rows_are_fatal(self):
        evidence = rule_evidence(_inventory(), [1])

        with self.assertRaisesRegex(ValueError, "unique and positive"):
            materialize_rule_items(evidence, [1, 1])
        with self.assertRaisesRegex(ValueError, "unique and positive"):
            materialize_rule_items(evidence, [0])

    def test_unknown_effect_reference_is_fatal(self):
        evidence = rule_evidence(_inventory(), [1])
        evidence["rows"]["R1"]["direct"] = ["E404"]

        with self.assertRaisesRegex(ValueError, "effect evidence missing"):
            materialize_rule_items(evidence, [1])

    def test_closed_effect_vocabulary_renders_without_interpretation(self):
        cases = (
            (
                StructuralEffect(
                    op="call", target="svc", value=None, from_source=None,
                    occurrence_id="call-1", call_source="svc()",
                ),
                'call("svc", None, [])',
            ),
            (StructuralEffect(op="ret", target=None, value="rc", from_source=True), 'ret(F("rc"))'),
            (StructuralEffect(op="ret", target=None, value=None, from_source=None), "ret(None)"),
            (StructuralEffect(op="set", target="x", value="1", from_source=True), 'set(F("x"), F("1"))'),
            (StructuralEffect(op="rollback", target=None, value=None, from_source=None), "rollback()"),
            (StructuralEffect(op="select", target="db:T", value=None, from_source=None), 'select("db:T")'),
        )
        for effect, expected in cases:
            with self.subTest(op=effect.op):
                self.assertEqual(expected, render_structural_effect(effect))

    def test_invalid_effect_positions_are_deterministic_invariant_failures(self):
        with self.assertRaisesRegex(ValueError, "invalid parser-owned Rule effect shape"):
            render_structural_effect({
                "op": "commit", "target": "db:T", "value": None,
                "from_source": None,
            })

    def test_provider_batching_and_retry_api_is_absent(self):
        for retired in (
            "collect_rules_by_rows", "plan_batches", "rule_queue_priority",
            "DEFAULT_ROWS_PER_CALL", "MAX_RULE_SEMANTIC_SLOTS",
        ):
            with self.subTest(retired=retired):
                self.assertFalse(hasattr(rule_row_batching, retired))


if __name__ == "__main__":
    unittest.main()
