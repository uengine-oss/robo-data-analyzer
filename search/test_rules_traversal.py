"""Retriever._rules_for — HAS_RULE→HAS_EXAMPLE→AFFECTS_TABLE 트래버설 단위 테스트.

아키텍트(cluster_retrieve)가 함수별 rules[{statement, examples[{given,when,then,tables}]}]
를 누락·오염 0 으로 받는지 검증. 실제 Neo4j 없이 fake session 의 .run().data() 로 검증.
"""
from __future__ import annotations

import unittest

from search.retriever import Retriever


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class _Session:
    """Fake Neo4j session — _rules_for 는 s.run(...).data() 만 사용."""

    def __init__(self, rows):
        self._rows = rows
        self.last_params: dict = {}
        self.last_cypher = ""

    def run(self, cypher, **params):
        self.last_cypher = cypher
        self.last_params = params
        return _Result(self._rows)


class RulesTraversalTest(unittest.TestCase):
    def _retriever(self, rows):
        return Retriever(_Session(rows), object())

    def test_groups_examples_under_rules_in_order(self):
        rows = [
            {"rule_id": "r1", "statement": "S1", "example_id": "fn::r1::1",
             "given": "g1", "when_": "w1", "then_": "t1",
             "tables": [{"name": "orders", "op": "INSERT"}]},
            {"rule_id": "r1", "statement": "S1", "example_id": "fn::r1::2",
             "given": "g2", "when_": "w2", "then_": "t2", "tables": []},
            {"rule_id": "r2", "statement": "S2", "example_id": "fn::r2::1",
             "given": "g3", "when_": "w3", "then_": "t3",
             "tables": [{"name": "stock", "op": "UPDATE"}]},
        ]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual([x["statement"] for x in out], ["S1", "S2"])
        self.assertEqual(len(out[0]["examples"]), 2)
        self.assertEqual(out[0]["examples"][0], {
            "given": "g1", "when": "w1", "then": "t1",
            "tables": [{"name": "orders", "op": "INSERT"}]})
        self.assertEqual(out[0]["examples"][1]["tables"], [])
        self.assertEqual(out[1]["examples"][0]["tables"], [{"name": "stock", "op": "UPDATE"}])

    def test_scopes_examples_to_this_function_via_prefix(self):
        r = self._retriever([])
        r._rules_for("member.c::checkout")
        # 공유 RULE 노드 오염 차단: 이 함수 소유 Example 만 prefix STARTS WITH 로 스코프
        self.assertEqual(r.s.last_params["prefix"], "member.c::checkout::")
        self.assertEqual(r.s.last_params["id"], "member.c::checkout")
        self.assertIn("STARTS WITH $prefix", r.s.last_cypher)
        # idx 숫자 정렬 계약: '::10' 이 '::2' 앞에 오지 않도록 size(e.id) 선행 정렬이어야 한다.
        self.assertIn("ORDER BY r.statement, size(e.id), e.id", r.s.last_cypher)

    def test_rule_without_examples_yields_empty_list(self):
        rows = [{"rule_id": "r1", "statement": "S1", "example_id": None,
                 "given": None, "when_": None, "then_": None, "tables": []}]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual(out, [{"statement": "S1", "examples": []}])

    def test_null_table_entries_dropped(self):
        rows = [{"rule_id": "r1", "statement": "S1", "example_id": "fn::r1::1",
                 "given": "g", "when_": "w", "then_": "t",
                 "tables": [{"name": None, "op": None}, {"name": "orders", "op": "READ"}]}]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual(out[0]["examples"][0]["tables"], [{"name": "orders", "op": "READ"}])

    def test_no_rules_returns_empty(self):
        self.assertEqual(self._retriever([])._rules_for("fn"), [])


if __name__ == "__main__":
    unittest.main()
