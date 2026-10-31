"""Retriever._rules_for — HAS_RULE→HAS_EXAMPLE→AFFECTS_TABLE 트래버설 단위 테스트.

아키텍트(cluster_retrieve)가 함수별 rules[{statement, examples[{given,when,then,tables}]}]
를 누락·오염 0 으로 받는지 검증. 실제 Neo4j 없이 fake session 의 .run().data() 로 검증.
"""
from __future__ import annotations

import unittest

from search.ranking.retriever import Retriever


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


def _row(rule_id, statement, example_id=None, *, occurrence_id=None, ordinal=0,
         given=None, when_=None, then_=None, table_name=None, op=None):
    """질의가 돌려주는 펼친 행 (규칙×예시×테이블 1행)."""
    return {
        "rule_id": rule_id, "occurrence_id": occurrence_id, "ordinal": ordinal,
        "statement": statement, "example_id": example_id,
        "given": given, "when_": when_, "then_": then_,
        "table_name": table_name, "op": op,
    }


class RulesTraversalTest(unittest.TestCase):
    def _retriever(self, rows):
        return Retriever(_Session(rows), object())

    def test_groups_examples_under_rules_in_order(self):
        rows = [
            _row("r2", "S2", "fn::r2::1", given="g3", when_="w3", then_="t3",
                 table_name="stock", op="UPDATE"),
            _row("r1", "S1", "fn::r1::1", given="g1", when_="w1", then_="t1",
                 table_name="orders", op="INSERT"),
            _row("r1", "S1", "fn::r1::2", given="g2", when_="w2", then_="t2"),
        ]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual([x["statement"] for x in out], ["S1", "S2"])
        self.assertEqual(len(out[0]["examples"]), 2)
        self.assertEqual(out[0]["examples"][0], {
            "given": "g1", "when": "w1", "then": "t1",
            "tables": [{"name": "orders", "op": "INSERT"}]})
        self.assertEqual(out[0]["examples"][1]["tables"], [])
        self.assertEqual(out[1]["examples"][0]["tables"], [{"name": "stock", "op": "UPDATE"}])

    def test_same_shared_rule_is_grouped_by_occurrence_without_example_cross_product(self):
        # 공유 RULE 은 두 출현의 예시를 모두 매단다 — 각 출현은 자기 occurrence_id 의 예시만 받는다.
        rows = [
            _row("shared", "S", "fn::R0001::shared::1", occurrence_id="fn::R0001", ordinal=1,
                 given="first", when_="w1", then_="t1"),
            _row("shared", "S", "fn::R0002::shared::1", occurrence_id="fn::R0001", ordinal=1,
                 given="second", when_="w2", then_="t2"),
            _row("shared", "S", "fn::R0001::shared::1", occurrence_id="fn::R0002", ordinal=2,
                 given="first", when_="w1", then_="t1"),
            _row("shared", "S", "fn::R0002::shared::1", occurrence_id="fn::R0002", ordinal=2,
                 given="second", when_="w2", then_="t2"),
        ]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual([rule["statement"] for rule in out], ["S", "S"])
        self.assertEqual([[e["given"] for e in rule["examples"]] for rule in out], [["first"], ["second"]])

    def test_duplicate_join_row_does_not_duplicate_one_example(self):
        row = _row("shared", "S", "fn::R0001::shared::1", occurrence_id="fn::R0001", ordinal=1,
                   given="g", when_="w", then_="t")
        out = self._retriever([row, dict(row)])._rules_for("fn")
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["examples"]), 1)

    def test_scopes_examples_to_this_function_via_prefix(self):
        node = "member.c::checkout"
        rows = [
            _row("r1", "S1", f"{node}::r1::10", given="ten"),
            _row("r1", "S1", "member.c::other::r1::1", given="foreign"),
            _row("r1", "S1", f"{node}::r1::2", given="two"),
        ]
        r = self._retriever(rows)
        out = r._rules_for(node)
        self.assertEqual(r.s.last_params["id"], node)
        # 공유 RULE 노드 오염 차단: 이 함수 소유 Example 만. idx 숫자 정렬('::2' 가 '::10' 앞).
        self.assertEqual([e["given"] for e in out[0]["examples"]], ["two", "ten"])
        # Ontological DB 가 거절하는 형태(속성 + 문자열, 맵 collect)를 질의에 다시 넣지 않는다.
        self.assertNotIn("+ '::'", r.s.last_cypher)
        self.assertNotIn("collect(", r.s.last_cypher)

    def test_rule_without_owned_examples_yields_empty_list(self):
        rows = [_row("r1", "S1"), _row("r2", "S2", "other::r2::1", given="foreign")]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual(out, [{"statement": "S1", "examples": []}, {"statement": "S2", "examples": []}])

    def test_null_table_entries_dropped(self):
        rows = [
            _row("r1", "S1", "fn::r1::1", given="g", when_="w", then_="t"),
            _row("r1", "S1", "fn::r1::1", given="g", when_="w", then_="t", table_name="orders", op="READ"),
        ]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual(out[0]["examples"][0]["tables"], [{"name": "orders", "op": "READ"}])

    def test_no_rules_returns_empty(self):
        self.assertEqual(self._retriever([])._rules_for("fn"), [])

if __name__ == "__main__":
    unittest.main()
