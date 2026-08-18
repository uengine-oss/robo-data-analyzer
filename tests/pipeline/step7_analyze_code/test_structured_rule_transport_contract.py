# -*- coding: utf-8 -*-
"""spec 116 — fanout 구조화 직렬화 ↔ example_mapping_builder 형식 계약 (중간 상태 부재 증명).

fanout이 만드는 구조화 transport를 빌더에 그대로 통과시켜 RULE/EXAMPLE/AFFECTS가
실제로 생성됨을 단언한다. 두 쪽 형식이 갈라지면 이 테스트가 즉시 깨진다 —
"조용히 RULE 0개" 중간 상태 금지.
"""
from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.models.code_analysis_output import ExampleItem, RuleItem
from pipeline.steps.step7_analyze_code.example_mapping_builder import (
    build_example_mapping,
)
from pipeline.steps.step7_analyze_code.rw_table_filter import (
    serialize_rules,
    rule_table_effects,
)


def _fn(rules: list[dict]) -> CodeNode:
    node = CodeNode(
        id="code:a.c:b100", signature="b100(itf)", file_path="a.c",
        start_line=1, end_line=9, label="FUNCTION", name="b100",
    )
    node.extra = {"rules": rules, "questions": [{"text": "q?", "reason": "r"}]}
    return node


class StructuredRuleTransportContract(unittest.TestCase):
    def _serialized(self):
        rules = [
            RuleItem(
                anchor_line=5,
                cond='F("a") == "1"',
                then=['set(F("dst"), "0000")', 'select("zord_acnt")', 'ret("rc")'],
            ),
        ]
        serialized, unresolved = serialize_rules(
            rules, "b100(itf)", reads=["table:PUB.ZORD_ACNT"], writes=[],
            table_op={"table:PUB.ZORD_ACNT": "READ"},
        )
        return serialized, unresolved

    def test_fanout_serialization_flows_into_builder_nodes(self):
        serialized, unresolved = self._serialized()
        self.assertEqual([], unresolved)
        result = build_example_mapping([_fn(serialized)])
        # RULE — 구조화 정체성·속성이 그대로 실린다 (조용한 0개 금지)
        self.assertEqual(1, len(result.rules))
        rule = result.rules[0]
        self.assertEqual("code:a.c:b100::R-5", rule.id)
        self.assertEqual("R-5", rule.rule_id)
        self.assertEqual(5, rule.anchor_line)
        self.assertEqual('F("a") == "1"', rule.cond)
        self.assertEqual("b100(itf)", rule.when)
        # EXAMPLE 은 LLM 이 아니라 cond/then 에서 결정론 합성된다 (spec 116 §21) —
        # 규칙을 만족하는 한 시나리오만 함께 실린다. 형제 else 규칙과 중복되는
        # 조건 불만족 산출물은 계약에서 제거됐다.
        self.assertEqual(["satisfy"], [e.kind for e in result.examples])
        self.assertEqual(["F('a') == '1'"], list(result.examples[0].given))
        # 규칙 단위 AFFECTS(결정론 귀속: select → READ, 권위 op=SCANNER)
        self.assertEqual(1, len(result.affects_table))
        affect = result.affects_table[0]
        self.assertEqual("table:PUB.ZORD_ACNT", affect.target_id)
        self.assertEqual("READ", affect.op)
        self.assertEqual("SCANNER", affect.op_source)
        # Question 경로도 함께 생존
        self.assertEqual(1, len(result.questions))

    def test_unknown_transport_shape_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "unknown rule transport"):
            build_example_mapping([_fn([{"weird": 1}])])

    def test_non_object_rule_fails_loudly(self):
        with self.assertRaisesRegex(TypeError, "rule transport entry"):
            build_example_mapping([_fn(["not-an-object"])])

    def test_nonempty_rule_prose_fails_loudly(self):
        serialized, _ = self._serialized()
        serialized[0]["nl"] = "조건을 해석한 문장"
        with self.assertRaisesRegex(ValueError, "Rule.nl must be the empty"):
            build_example_mapping([_fn(serialized)])

    def test_invalid_derived_example_fails_loudly(self):
        serialized, _ = self._serialized()
        serialized[0]["examples"] = ["not-an-object"]
        with self.assertRaisesRegex(TypeError, "example transport entry"):
            build_example_mapping([_fn(serialized)])

    def test_invalid_question_fails_loudly(self):
        serialized, _ = self._serialized()
        fn = _fn(serialized)
        fn.extra["questions"] = [{"text": "", "reason": "missing"}]
        with self.assertRaisesRegex(ValueError, "empty question transport"):
            build_example_mapping([fn])

    def test_legacy_statement_shape_fails_loudly(self):
        # 자연어 statement 형식은 계약에서 삭제됐다 — 되살아나면 조용한 RULE 0 이 아니라
        # 즉시 실패해야 한다.
        legacy = [{
            "statement": "레거시 규칙", "anchor_line": 3,
            "examples": [{"given": "g", "when": "w", "then": "t",
                          "affected_tables": []}],
        }]
        with self.assertRaisesRegex(ValueError, "unknown rule transport"):
            build_example_mapping([_fn(legacy)])

    def test_unresolved_table_name_is_surfaced_not_invented(self):
        effects, unresolved = rule_table_effects(
            ['insert("nope_table")'], reads=[], writes=["table:PUB.REAL"],
            table_op=None,
        )
        self.assertEqual([], effects)
        self.assertEqual(["nope_table"], unresolved)


if __name__ == "__main__":
    unittest.main()
