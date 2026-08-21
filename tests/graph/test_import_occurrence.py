from __future__ import annotations

import unittest

from graph.nodes.import_occurrence import upsert_import_occurrences


def _row(import_id: str = "a" * 64) -> dict:
    return {
        "occurrence_id": f"{import_id}:0", "import_id": import_id,
        "ordinal": 0, "entry_ordinal": 0, "evidence_version": "1.1.0",
        "source_id": "a.c", "file": "a.c", "line": 1, "end_line": 1,
        "range": {"char_offset": 0, "char_length": 20},
        "directive_kind": "include", "grammar_rule": "includeLine",
        "presence_status": "active",
        "presence": {"status": "active", "provenance": "unconditional"},
        "evidence_status": "complete", "import_kind": "source_file",
        "target_kind": "quoted", "target": '"common.h"',
        "target_range": {"char_offset": 9, "char_length": 10},
        "path_components": ["common.h"],
        "path_component_ranges": [{"char_offset": 10, "char_length": 8}],
        "member": None, "member_range": None, "alias": None,
        "alias_range": None, "relative_level": 0, "wildcard": False,
        "locality": "local", "outcome": "unresolved",
        "reason": "insufficient_import_binding_environment",
        "resolved_module_ids": [],
    }


class _Client:
    generation_id = "run:new"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute_write(self, callback):
        return await callback(self)

    async def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return self

    async def consume(self):
        return None


class ImportOccurrenceWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_ledger_is_written_as_generation_owned_nodes(self):
        client = _Client()

        count = await upsert_import_occurrences(client, [_row()])

        self.assertEqual(1, count)
        self.assertEqual(1, len(client.calls))
        query, parameters = client.calls[0]
        self.assertIn("IMPORT_OCCURRENCE", query)
        self.assertEqual(
            f"import-occurrence:{'a' * 64}:0", parameters["items"][0]["id"],
        )

    async def test_duplicate_fact_entry_identity_fails_before_write(self):
        client = _Client()
        with self.assertRaisesRegex(ValueError, "duplicate identities"):
            await upsert_import_occurrences(client, [_row(), _row()])
        self.assertEqual([], client.calls)


if __name__ == "__main__":
    unittest.main()
