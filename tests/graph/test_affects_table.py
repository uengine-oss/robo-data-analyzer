"""AFFECTS_TABLE exact endpoint identity contract tests."""

import unittest

from graph.edges.affects_table import _UPSERT_QUERY, upsert_affects_table
from graph.schema.graph_edges import AffectsTable


class _RecordingAnalysisGraphDatabase:
    def __init__(self) -> None:
        self.query = ""
        self.items: list[dict] = []

    async def run_batch_unwind(self, query: str, items: list[dict]) -> None:
        self.query = query
        self.items = items


class AffectsTableContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_table_id_and_owner_are_preserved(self) -> None:
        graph_database = _RecordingAnalysisGraphDatabase()
        await upsert_affects_table(graph_database, [
            AffectsTable(
                source_id="code:orders::rule::1",
                target_id="db:shop.public.orders",
                op="READ",
            ),
        ])

        self.assertEqual(graph_database.items, [{
            "source_id": "code:orders::rule::1",
            "target_id": "db:shop.public.orders",
            "op": "READ",
        }])
        self.assertIn("t:TABLE {id: item.target_id", graph_database.query)
        self.assertIn("graph_owner: 'analyzer'", graph_database.query)
        self.assertNotIn("t.name", graph_database.query)
        self.assertNotIn("split(", graph_database.query)

    def test_query_constant_uses_exact_identity(self) -> None:
        self.assertIn("item.target_id", _UPSERT_QUERY)


if __name__ == "__main__":
    unittest.main()
