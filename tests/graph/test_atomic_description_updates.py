from __future__ import annotations

import unittest

from graph.neo4j_client import Neo4jClient
from graph.nodes.column import update_analyzed_descriptions as update_columns
from graph.nodes.table import update_analyzed_descriptions as update_tables


class _Result:
    def __init__(self, row: dict | None = None) -> None:
        self.row = row
        self.consumed = False

    async def single(self, strict: bool = False):
        if strict and self.row is None:
            raise ValueError("expected one guard row")
        return self.row

    async def consume(self):
        self.consumed = True


class _Transaction:
    def __init__(self, matched: int) -> None:
        self.matched = matched
        self.calls: list[tuple[str, dict]] = []
        self.write_result = _Result()

    async def run(self, query: str, **params):
        self.calls.append((query, params))
        if "RETURN count" in query:
            return _Result({"matched": self.matched})
        return self.write_result


class _Session:
    def __init__(self, tx: _Transaction) -> None:
        self.tx = tx

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute_write(self, callback):
        return await callback(self.tx)


class _Driver:
    def __init__(self, tx: _Transaction) -> None:
        self.tx = tx

    def session(self, *, database: str):
        return _Session(self.tx)


def _client(matched: int) -> tuple[Neo4jClient, _Transaction]:
    tx = _Transaction(matched)
    client = object.__new__(Neo4jClient)
    client._driver = _Driver(tx)
    client._database = "test"
    client._closed = False
    return client, tx


class GuardedBatchUnwindTest(unittest.IsolatedAsyncioTestCase):
    async def test_guard_and_write_share_one_transaction(self):
        client, tx = _client(matched=2)
        matched = await client.run_guarded_batch_unwind(
            guard_query="MATCH (n) WHERE n.id IN $ids RETURN count(n) AS matched",
            write_query="UNWIND $items AS item MATCH (n {id: item.id}) SET n.x = item.x",
            items=[{"id": "a", "x": 1}, {"id": "b", "x": 2}],
        )
        self.assertEqual(matched, 2)
        self.assertEqual(len(tx.calls), 2)
        self.assertEqual(tx.calls[0][1]["ids"], ["a", "b"])
        self.assertTrue(tx.write_result.consumed)

    async def test_guard_mismatch_rolls_back_before_write(self):
        client, tx = _client(matched=1)
        with self.assertRaisesRegex(ValueError, "matched 1 of 2"):
            await client.run_guarded_batch_unwind(
                guard_query="MATCH (n) WHERE n.id IN $ids RETURN count(n) AS matched",
                write_query="UNWIND $items AS item MATCH (n {id: item.id}) SET n.x = item.x",
                items=[{"id": "a"}, {"id": "missing"}],
            )
        self.assertEqual(len(tx.calls), 1)

    async def test_table_and_column_writers_use_guarded_contract(self):
        table_client, table_tx = _client(matched=1)
        column_client, column_tx = _client(matched=1)
        table_update = [{"id": "db:s.t", "logical_name": "T", "analyzed_description": "table"}]
        column_update = [{"id": "db:s.t.c", "logical_name": "C", "analyzed_description": "column"}]

        self.assertEqual(await update_tables(table_client, table_update), 1)
        self.assertEqual(await update_columns(column_client, column_update), 1)
        self.assertEqual(len(table_tx.calls), 2)
        self.assertEqual(len(column_tx.calls), 2)

    async def test_duplicate_update_ids_are_rejected_before_query(self):
        client, tx = _client(matched=1)
        duplicate = [
            {"id": "db:s.t", "analyzed_description": "a"},
            {"id": "db:s.t", "analyzed_description": "b"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate update IDs"):
            await client.run_guarded_batch_unwind(
                guard_query="RETURN count(*) AS matched",
                write_query="RETURN 1",
                items=duplicate,
            )
        self.assertEqual(tx.calls, [])


if __name__ == "__main__":
    unittest.main()
