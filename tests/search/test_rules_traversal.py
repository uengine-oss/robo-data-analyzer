from __future__ import annotations

import unittest

from search.ranking.graph_retriever import Retriever


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.last_params = {}
        self.last_cypher = ""

    def run(self, cypher, **params):
        self.last_cypher = cypher
        self.last_params = params
        return _Result(self.rows)


class RulesTraversalTest(unittest.TestCase):
    def _retriever(self, rows):
        return Retriever(_Session(rows), object())

    def test_returns_direct_rules(self):
        rows = [{
            "rule_id": "fn::R-3",
            "rule_order": 1,
            "line": 3,
            "condition": 'F("cd") == "3"',
            "effects": ['ret("0")'],
            "condition_description": "",
            "effect_descriptions": ["정상 결과를 반환한다"],
        }]
        out = self._retriever(rows)._rules_for("fn")
        self.assertEqual(rows, out)
        self.assertEqual(
            {"rule_id", "rule_order", "line", "condition", "effects",
             "condition_description", "effect_descriptions"},
            set(out[0]),
        )

    def test_query_uses_direct_identity_and_source_order(self):
        retriever = self._retriever([])
        retriever._rules_for("member.c::checkout")
        self.assertEqual("member.c::checkout", retriever.s.last_params["node_id"])
        self.assertIn("ORDER BY rule_order, line", retriever.s.last_cypher)
        self.assertIn("r._id", retriever.s.last_cypher)
        self.assertIn("HAS_RULE", retriever.s.last_cypher)

    def test_no_rules_returns_empty(self):
        self.assertEqual([], self._retriever([])._rules_for("fn"))


if __name__ == "__main__":
    unittest.main()
