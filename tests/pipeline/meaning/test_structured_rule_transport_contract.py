"""Rule serialization and graph projection share one closed transport."""
from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.models.code_analysis_output import RuleItem
from pipeline.meaning.rule.rule_graph import build_rule_graph
from pipeline.meaning.table.rule_tables import rule_table_accesses, serialize_rules


def _function(rules: list[dict]) -> CodeNode:
    node = CodeNode(
        id="code:a.c:b100",
        signature="b100(itf)",
        file_path="a.c",
        start_line=1,
        end_line=9,
        label="FUNCTION",
        name="b100",
    )
    node.extra = {"rules": rules}
    return node


class StructuredRuleTransportContract(unittest.TestCase):
    def _serialized(self):
        rules = [RuleItem(
            anchor_line=5,
            structural_condition='F("a") == "1"',
            structural_effects=[
                'set(F("dst"), "0000")',
                'select("zord_acnt")',
                'ret("rc")',
            ],
            condition_description="처리 대상 값이 1일 때",
            effect_descriptions=["정상 코드를 설정하고 계좌를 조회한다"],
        )]
        return serialize_rules(
            rules,
            reads=["table:PUB.ZORD_ACNT"],
            writes=[],
            table_op={"table:PUB.ZORD_ACNT": "READ"},
        )

    def test_serialization_flows_into_rule_nodes_and_table_edges(self):
        serialized, unresolved = self._serialized()
        self.assertEqual([], unresolved)

        result = build_rule_graph([_function(serialized)])

        self.assertEqual(1, len(result.rules))
        rule = result.rules[0]
        self.assertEqual("code:a.c:b100::R-5", rule.id)
        self.assertEqual('F("a") == "1"', rule.structural_condition)
        self.assertEqual("처리 대상 값이 1일 때", rule.condition_description)
        self.assertEqual(
            ("정상 코드를 설정하고 계좌를 조회한다",),
            rule.effect_descriptions,
        )
        self.assertEqual(1, len(result.table_edges))
        table_edge = result.table_edges[0]
        self.assertEqual("READS", table_edge.relation)
        self.assertEqual("table:PUB.ZORD_ACNT", table_edge.target_id)
        self.assertEqual(("READ",), table_edge.operations)

    def test_unknown_transport_shape_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "unknown Rule transport"):
            build_rule_graph([_function([{"weird": 1}])])

    def test_non_object_rule_fails_loudly(self):
        with self.assertRaisesRegex(TypeError, "Rule transport entry"):
            build_rule_graph([_function(["not-an-object"])])

    def test_structure_only_candidate_cannot_cross_product_boundary(self):
        with self.assertRaisesRegex(ValueError, "effect description"):
            build_rule_graph([_function([{
                "anchor_line": 5,
                "structural_condition": 'F("ready")',
                "structural_effects": ['call("work", None, [])'],
                "condition_description": "준비되었을 때",
                "effect_descriptions": [],
                "table_accesses": [],
            }])])

    def test_removed_field_is_not_forwarded(self):
        serialized, _ = self._serialized()
        serialized[0]["nl"] = "옛 설명"
        with self.assertRaisesRegex(ValueError, "unknown Rule transport fields"):
            build_rule_graph([_function(serialized)])

    def test_invalid_table_access_fails_loudly(self):
        serialized, _ = self._serialized()
        serialized[0]["table_accesses"] = [{"table": "orders", "access": "MAYBE"}]
        with self.assertRaisesRegex(ValueError, "invalid Rule table access"):
            build_rule_graph([_function(serialized)])

    def test_legacy_statement_shape_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "unknown Rule transport"):
            build_rule_graph([_function([{
                "statement": "옛 규칙",
                "anchor_line": 3,
            }])])

    def test_unresolved_table_name_is_surfaced_not_invented(self):
        accesses, unresolved = rule_table_accesses(
            ['insert("nope_table")'],
            reads=[],
            writes=["table:PUB.REAL"],
            table_op=None,
        )
        self.assertEqual([], accesses)
        self.assertEqual(["nope_table"], unresolved)


if __name__ == "__main__":
    unittest.main()
