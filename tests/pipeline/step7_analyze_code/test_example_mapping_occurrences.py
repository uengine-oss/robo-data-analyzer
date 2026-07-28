from __future__ import annotations

import unittest
from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    ExampleItem,
    RuleItem,
    RulesExamplesOutput,
    require_unique_rule_occurrences,
)
from pipeline.steps.step7_analyze_code.example_mapping_builder import (
    build_example_mapping,
)


class ExampleMappingOccurrenceTest(unittest.TestCase):
    def test_identical_statements_keep_every_local_occurrence_and_example(self):
        fn = CodeNode(
            id="function:checkout",
            signature="checkout()",
            file_path="checkout.py",
            start_line=1,
            end_line=20,
            label="FUNCTION",
            extra={
                "rules": [
                    {
                        "statement": "승인되면 결과를 반환한다.",
                        "anchor_line": 3,
                        "examples": [
                            {"given": "첫 요청", "when": "승인", "then": "첫 결과"},
                            {"given": "재요청", "when": "승인", "then": "재요청 결과"},
                        ],
                    },
                    {
                        "statement": "승인되면 결과를 반환한다.",
                        "anchor_line": 12,
                        "examples": [
                            {"given": "다른 경로", "when": "승인", "then": "다른 결과"},
                        ],
                    },
                ]
            },
        )

        result = build_example_mapping([fn])

        self.assertEqual(len(result.rules), 1)
        self.assertEqual(len(result.has_rule), 2)
        self.assertEqual(
            [edge.occurrence_id for edge in result.has_rule],
            ["function:checkout::R0001", "function:checkout::R0002"],
        )
        self.assertEqual([edge.anchor_line for edge in result.has_rule], [3, 12])
        self.assertEqual(len(result.examples), 3)
        self.assertEqual(len({example.id for example in result.examples}), 3)
        self.assertEqual(
            [edge.occurrence_id for edge in result.has_example],
            [
                "function:checkout::R0001",
                "function:checkout::R0001",
                "function:checkout::R0002",
            ],
        )
        self.assertEqual(
            [edge.ordinal for edge in result.has_example],
            [1, 2, 1],
        )
        self.assertTrue(result.examples[0].id.startswith("function:checkout::R0001::"))
        self.assertTrue(result.examples[2].id.startswith("function:checkout::R0002::"))

    def test_same_meaning_at_different_anchors_is_valid(self):
        rules = [
            RuleItem(
                statement="허용되면 결과를 반환한다.",
                anchor_line=line,
                examples=[ExampleItem(given="허용 상태", when="요청", then="결과 반환")],
            )
            for line in (4, 19)
        ]
        self.assertEqual(2, len(RulesExamplesOutput(rules=rules).rules))
        self.assertEqual(
            2,
            len(BatchRulesItem(node_key="1-20", rules=rules).rules),
        )

    def test_exact_same_anchor_and_scenarios_are_rejected(self):
        rule = RuleItem(
            statement="허용되면 결과를 반환한다.",
            anchor_line=4,
            examples=[ExampleItem(given="허용 상태", when="요청", then="결과 반환")],
        )
        with self.assertRaises(ValidationError):
            RulesExamplesOutput(rules=[rule, rule])

        # DBMS는 배치 전체를 잃지 않도록 item 파싱 뒤 fan-in에서 이 검증을 적용한다.
        batch_item = BatchRulesItem(node_key="1-20", rules=[rule, rule])
        with self.assertRaises(ValueError):
            require_unique_rule_occurrences(batch_item.rules)

    def test_anchor_is_required_positive_strict_integer(self):
        for value in (None, 0, -1, True, "7"):
            with self.subTest(value=value):
                payload = {
                    "statement": "항상 결과를 반환한다.",
                    "examples": [],
                }
                if value is not None:
                    payload["anchor_line"] = value
                with self.assertRaises(ValidationError):
                    RuleItem.model_validate(payload)

    def test_builder_rejects_legacy_zero_anchor_instead_of_coercing_it(self):
        fn = CodeNode(
            id="function:legacy",
            signature="legacy()",
            file_path="legacy.c",
            start_line=1,
            end_line=3,
            label="FUNCTION",
            extra={
                "rules": [{
                    "statement": "항상 결과를 반환한다.",
                    "anchor_line": 0,
                    "examples": [],
                }]
            },
        )
        with self.assertRaises(ValueError):
            build_example_mapping([fn])


if __name__ == "__main__":
    unittest.main()
