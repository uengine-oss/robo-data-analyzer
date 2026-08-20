from __future__ import annotations

import unittest

from graph.nodes.call_occurrence import upsert_call_occurrences


def _row(call_id: str = "call-1", caller_id: str = "code:f") -> dict:
    return {
        "call_id": call_id,
        "ordinal": 0,
        "source_id": "source:a.c",
        "caller_id": caller_id,
        "owner_status": "resolved" if caller_id else "unresolved_no_routine",
        "file": "a.c",
        "line": 3,
        "range": {"char_offset": 12, "char_length": 8},
        "callee_range": {"char_offset": 12, "char_length": 4},
        "argument_ranges": [{"char_offset": 17, "char_length": 2}],
        "callee_kind": "named",
        "terminal_name": "work",
        "grammar_rule": "postfixExpression",
        "presence_status": "active",
        "presence": {"status": "active", "condition": None,
                     "configurationId": None, "provenance": "unconditional"},
        "configured_preprocessing_required": False,
        "outcome": "unresolved",
        "reason": "insufficient_wrapper_argument_syntax_evidence",
        "preprocessing_evidence_ids": [],
        "macro_fact_ids": [],
    }


class _Result:
    def __init__(self, *, record=None):
        self.record = record
        self.consumed = False

    async def single(self, strict=False):
        del strict
        return self.record

    async def consume(self):
        self.consumed = True


class _Transaction:
    def __init__(self, matched: int = 1):
        self.matched = matched
        self.calls: list[tuple[str, dict]] = []

    async def run(self, query, **parameters):
        self.calls.append((query, parameters))
        if "RETURN count" in query:
            return _Result(record={"matched": self.matched})
        return _Result()


class _Client:
    generation_id = "run:1"

    def __init__(self, transaction):
        self.transaction = transaction

    async def execute_write(self, callback):
        return await callback(self.transaction)


class CallOccurrenceWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_guard_node_and_edge_share_one_transaction(self):
        tx = _Transaction()
        matched = await upsert_call_occurrences(_Client(tx), [_row()])

        self.assertEqual(1, matched)
        self.assertEqual(3, len(tx.calls))
        self.assertIn("CALL_OCCURRENCE", tx.calls[1][0])
        self.assertIn("HAS_CALL_OCCURRENCE", tx.calls[2][0])
        [item] = tx.calls[1][1]["items"]
        self.assertEqual("call-occurrence:call-1", item["id"])
        self.assertEqual("call-1", item["call_id"])

    async def test_missing_owner_aborts_before_any_write(self):
        tx = _Transaction(matched=0)
        with self.assertRaisesRegex(ValueError, "matched 0 of 1"):
            await upsert_call_occurrences(_Client(tx), [_row()])
        self.assertEqual(1, len(tx.calls))

    async def test_duplicate_call_ids_are_rejected_before_transaction(self):
        tx = _Transaction()
        with self.assertRaisesRegex(ValueError, "duplicate canonical call_id"):
            await upsert_call_occurrences(_Client(tx), [_row(), _row()])
        self.assertEqual([], tx.calls)


if __name__ == "__main__":
    unittest.main()
