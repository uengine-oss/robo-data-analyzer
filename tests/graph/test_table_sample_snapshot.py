"""Analysis-time datasource samples are persisted once for fast read-only GWT use."""
from __future__ import annotations

import json
import unittest

from graph.nodes.table import update_sample_snapshots


class _Recorder:
    def __init__(self) -> None:
        self.guard_query = ""
        self.write_query = ""
        self.items: list[dict] = []

    async def run_guarded_batch_unwind(self, *, guard_query, write_query, items):
        self.guard_query = guard_query
        self.write_query = write_query
        self.items = items
        return len(items)


class TableSampleSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def test_compact_unicode_snapshot_uses_guarded_existing_tables(self):
        client = _Recorder()
        matched = await update_sample_snapshots(client, {
            "db:shop.public.orders": {
                "columns": [{"name": "STATUS"}],
                "sample_rows": [{"STATUS": "결제완료"}],
            },
        })
        self.assertEqual(1, matched)
        self.assertIn("graph_owner: 'analyzer'", client.guard_query)
        self.assertIn("sample_snapshot_json", client.write_query)
        snapshot = client.items[0]["sample_snapshot_json"]
        self.assertNotIn("\\u", snapshot)
        self.assertEqual(
            [{"STATUS": "결제완료"}], json.loads(snapshot)["sample_rows"],
        )

    async def test_empty_snapshot_is_noop(self):
        client = _Recorder()
        self.assertEqual(0, await update_sample_snapshots(client, {}))
        self.assertEqual([], client.items)
