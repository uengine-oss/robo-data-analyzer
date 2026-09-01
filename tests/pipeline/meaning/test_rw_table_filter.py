"""Rule table accesses are derived from validated leaves and direct R/W."""
from __future__ import annotations

import unittest

from pipeline.meaning.table.rule_tables import rule_table_accesses


class RuleTableAccessesTest(unittest.TestCase):
    def test_resolves_by_last_segment(self):
        effects, unresolved = rule_table_accesses(
            ['select("zord_acnt")', 'ret("rc")'],
            reads=["table:PUB.ZORD_ACNT"], writes=[], table_op=None,
        )
        self.assertEqual([], unresolved)
        self.assertEqual([{
            "table": "table:PUB.ZORD_ACNT",
            "access": "READ",
            "operation": "READ",
        }], effects)

    def test_model_effect_cannot_be_rewritten_to_authoritative_op(self):
        effects, unresolved = rule_table_accesses(
            ['update("hist")'],
            reads=[], writes=["table:PUB.HIST"],
            table_op={"table:PUB.HIST": "SELECT"},
        )
        self.assertEqual([], effects)
        self.assertEqual(["hist"], unresolved)

    def test_upsert_preserves_authoritative_merge(self):
        effects, unresolved = rule_table_accesses(
            ['upsert("hist")'],
            reads=[], writes=["table:PUB.HIST"],
            table_op={"table:PUB.HIST": "MERGE"},
        )
        self.assertEqual([], unresolved)
        self.assertEqual("MERGE", effects[0]["operation"])

    def test_unresolved_name_is_surfaced_not_invented(self):
        effects, unresolved = rule_table_accesses(
            ['insert("ghost")'],
            reads=[], writes=["table:PUB.REAL"], table_op=None,
        )
        self.assertEqual([], effects)
        self.assertEqual(["ghost"], unresolved)

    def test_invalid_effect_is_not_swallowed(self):
        with self.assertRaises(ValueError):
            rule_table_accesses(
                ['select("orders"), delete("orders")'],
                reads=["orders"], writes=["orders"], table_op=None,
            )


if __name__ == "__main__":
    unittest.main()
