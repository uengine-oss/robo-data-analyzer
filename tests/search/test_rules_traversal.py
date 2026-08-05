"""Retriever._rules_for — HAS_RULE→AFFECTS_TABLE→HAS_EXAMPLE 트래버설 단위 테스트.

아키텍트(cluster_retrieve)가 함수별 rules[{rule_id, anchor_line, cond, then, nl, when,
tables, examples[{kind, given, then}]}] (파이썬 leaf 계약, spec 116)를 누락·오염 0 으로
받는지 검증. 실제 Neo4j 없이 fake session 의 .run().data() 로 검증.
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


def _row(rule, anchor, **overrides):
    base = {
        "rule_node_id": f"fn::{rule}",
        "rule_id": rule,
        "ordinal": anchor,
        "anchor_line": anchor,
        "cond": f'F("cd") == "{anchor}"',
        "effects": ['ret("0")'],
        "nl": f"규칙 {rule}",
        "when": "fn(itf)",
        "example_id": None,
        "kind": None,
        "given": None,
        "example_effects": None,
        "tables": [],
    }
    base.update(overrides)
    return base


class RulesTraversalTest(unittest.TestCase):
    def _retriever(self, rows):
        return Retriever(_Session(rows), object())

    def test_groups_examples_under_rules_in_order(self):
        rows = [
            _row("R-3", 3, example_id="fn::R-3::E1", kind="satisfy",
                 given=['F("cd") == "3"'], example_effects=['ret("0")'],
                 tables=[{"name": "orders", "op": "INSERT"}]),
            _row("R-9", 9, example_id="fn::R-9::E1", kind="satisfy",
                 given=['F("cd") == "9"'], example_effects=['ret("1")'],
                 tables=[{"name": "stock", "op": "UPDATE"}]),
        ]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual([x["rule_id"] for x in out], ["R-3", "R-9"])
        self.assertEqual([x["anchor_line"] for x in out], [3, 9])
        self.assertEqual(len(out[0]["examples"]), 1)
        self.assertEqual(out[0]["examples"][0], {
            "kind": "satisfy", "given": ['F("cd") == "3"'], "then": ['ret("0")'],
        })
        # 테이블 귀속은 규칙 단위다 — example 이 아니라 rule 에 붙는다.
        self.assertEqual(out[0]["tables"], [{"name": "orders", "op": "INSERT"}])
        self.assertEqual(out[1]["tables"], [{"name": "stock", "op": "UPDATE"}])

    def test_duplicate_join_row_does_not_duplicate_one_example(self):
        row = _row("R-3", 3, example_id="fn::R-3::E1", kind="satisfy",
                   given=["g"], example_effects=["t"])
        out = self._retriever([row, dict(row)])._rules_for("fn")
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["examples"]), 1)

    def test_query_scopes_by_owner_and_orders_by_anchor(self):
        r = self._retriever([])
        r._rules_for("member.c::checkout")
        self.assertEqual(r.s.last_params["id"], "member.c::checkout")
        # idx 숫자 정렬 계약: '::E10' 이 '::E2' 앞에 오지 않도록 size 선행 정렬.
        self.assertIn("ORDER BY ordinal, anchor_line", r.s.last_cypher)
        self.assertIn("size(coalesce(e.id,''))", r.s.last_cypher)

    def test_rule_without_examples_yields_machine_body_only(self):
        out = self._retriever([_row("R-3", 3)])._rules_for("fn")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["examples"], [])
        self.assertEqual(out[0]["cond"], 'F("cd") == "3"')
        self.assertEqual(out[0]["then"], ['ret("0")'])
        self.assertEqual(out[0]["when"], "fn(itf)")

    def test_null_table_entries_dropped(self):
        rows = [_row("R-3", 3, tables=[
            {"name": None, "op": None}, {"name": "orders", "op": "READ"},
        ])]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual(out[0]["tables"], [{"name": "orders", "op": "READ"}])

    def test_no_rules_returns_empty(self):
        self.assertEqual(self._retriever([])._rules_for("fn"), [])


if __name__ == "__main__":
    unittest.main()
