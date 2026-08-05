"""Rule anchor 정체성 계약 — anchor 는 양의 절대 줄 정수이며 빌더가 강제한다 (spec 116).

폐기된 statement 해시 공유·완전 중복 거부 의미론은 anchor 정체성으로 대체됐다 —
중복 anchor 검출은 채점기(rule_grading)·배치 사전검사(_rules_leaf_ok)의 몫이다.
"""
from __future__ import annotations

import unittest
from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.code_analysis_output import RuleItem
from pipeline.steps.step7_analyze_code.example_mapping_builder import (
    build_example_mapping,
)


class RuleAnchorContractTest(unittest.TestCase):
    def test_anchor_is_required_positive_strict_integer(self):
        for value in (None, 0, -1, True, "7"):
            with self.subTest(value=value):
                payload = {"cond": None, "then": [], "examples": []}
                if value is not None:
                    payload["anchor_line"] = value
                with self.assertRaises(ValidationError):
                    RuleItem.model_validate(payload)

    def test_builder_rejects_zero_anchor_instead_of_coercing_it(self):
        fn = CodeNode(
            id="function:legacy",
            signature="legacy()",
            file_path="legacy.c",
            start_line=1,
            end_line=3,
            label="FUNCTION",
            extra={
                "rules": [{
                    "rule_id": "R-0",
                    "anchor_line": 0,
                    "cond": "",
                    "then": [],
                    "examples": [],
                }]
            },
        )
        with self.assertRaises(ValueError):
            build_example_mapping([fn])


if __name__ == "__main__":
    unittest.main()
