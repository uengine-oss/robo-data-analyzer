from __future__ import annotations

import unittest

from graph.schema.graph_edges import HasExample, HasRule
from graph.schema.graph_nodes import CodeNode, Example, Rule
from pipeline.publication.rule_examples import (
    RuleExamplesProjectionError,
    build_examples_write_batch,
    build_rule_meanings_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


def _function() -> CodeNode:
    return CodeNode(
        id="code:orders.validate",
        signature="validate(ctx)",
        file_path="orders.c",
        start_line=10,
        end_line=50,
        label="FUNCTION",
        name="validate",
        owner_id="module:orders.c",
    )


def _rule(function: CodeNode, anchor: int = 21) -> Rule:
    return Rule(
        id=f"{function.id}::R-{anchor}",
        anchor_line=anchor,
        structural_condition="ctx.status == 'READY'",
        structural_effects=("return 0",),
        business_effects=("order.accept()",),
    )


class RuleExamplesPublicationTest(unittest.TestCase):
    def test_rule_and_example_have_relationship_owned_identity_and_order(self) -> None:
        function = _function()
        rule = _rule(function)
        rule_batch = build_rule_meanings_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[function],
            rules=[rule],
            has_rule_edges=[HasRule(function.id, rule.id, 1)],
        )
        example = Example(
            id=f"{rule.id}::E1",
            given_conditions=("order.status == 'ready'",),
            expected_effects=("order.accept()",),
        )
        example_batch = build_examples_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            rules=[rule],
            examples=[example],
            has_example_edges=[HasExample(rule.id, example.id, 1)],
        )

        rule_properties = dict(rule_batch.node_create_operations[0].present_properties)
        self.assertNotIn("owner_id", rule_properties)
        self.assertNotIn("business_meaning_status", rule_properties)
        self.assertEqual(
            "HAS_RULE",
            rule_batch.relationship_create_operations[0].identity.relationship_type,
        )
        self.assertEqual(
            "HAS_EXAMPLE",
            example_batch.relationship_create_operations[0].identity.relationship_type,
        )

    def test_rule_ordinals_must_be_contiguous_and_owner_must_be_routine(self) -> None:
        function = _function()
        rule = _rule(function)
        with self.assertRaisesRegex(RuleExamplesProjectionError, "contiguous"):
            build_rule_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[function],
                rules=[rule],
                has_rule_edges=[HasRule(function.id, rule.id, 2)],
            )

        function.label = "IF"
        with self.assertRaisesRegex(RuleExamplesProjectionError, "verified routine"):
            build_rule_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[function],
                rules=[rule],
                has_rule_edges=[HasRule(function.id, rule.id, 1)],
            )

    def test_rule_requires_verified_business_meaning(self) -> None:
        function = _function()
        rule = Rule(
            id=f"{function.id}::R-21",
            anchor_line=21,
            structural_condition="ctx.status == 'READY'",
        )
        with self.assertRaisesRegex(RuleExamplesProjectionError, "no verified business"):
            build_rule_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[function],
                rules=[rule],
                has_rule_edges=[HasRule(function.id, rule.id, 1)],
            )

    def test_example_requires_exact_owner_and_nonempty_python_leaf(self) -> None:
        function = _function()
        rule = _rule(function)
        example = Example(id=f"{rule.id}::E1")
        with self.assertRaisesRegex(RuleExamplesProjectionError, "non-blank"):
            build_examples_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                rules=[rule],
                examples=[example],
                has_example_edges=[HasExample(rule.id, example.id, 1)],
            )

    def test_extra_owner_edge_or_orphan_is_rejected(self) -> None:
        function = _function()
        rule = _rule(function)
        with self.assertRaisesRegex(RuleExamplesProjectionError, "target is missing"):
            build_rule_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[function],
                rules=[rule],
                has_rule_edges=[
                    HasRule(function.id, rule.id, 1),
                    HasRule(function.id, "rule:unknown", 2),
                ],
            )


if __name__ == "__main__":
    unittest.main()
