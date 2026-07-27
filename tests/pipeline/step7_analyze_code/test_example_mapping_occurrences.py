from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
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
        self.assertTrue(result.examples[0].id.startswith("function:checkout::R0001::"))
        self.assertTrue(result.examples[2].id.startswith("function:checkout::R0002::"))


if __name__ == "__main__":
    unittest.main()
