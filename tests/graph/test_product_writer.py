from __future__ import annotations

import unittest

from graph.product_graph import ProductGraph, ProductNode, ProductRelationship
from graph.product_writer import replace_product_graph


class _Result:
    def __init__(self, row=None):
        self.row = row or {}

    async def single(self, *, strict=False):
        return self.row


class _Transaction:
    def __init__(self):
        self.calls = []

    async def run(self, query, **parameters):
        self.calls.append((query, parameters))
        items = parameters.get("items") or parameters.get("ids") or []
        if "AS written" in query:
            return _Result({"written": len(items)})
        if "AS matched" in query:
            return _Result({"matched": len(items)})
        return _Result()


class _Client:
    def __init__(self):
        self.schema_queries = []
        self.transaction = _Transaction()
        self.write_count = 0

    async def run_query(self, query):
        self.schema_queries.append(query)

    async def execute_write(self, work):
        self.write_count += 1
        return await work(self.transaction)


class ProductWriterTest(unittest.IsolatedAsyncioTestCase):
    async def test_complete_graph_is_written_in_one_data_transaction(self) -> None:
        graph = ProductGraph.build(
            nodes=(
                ProductNode.build(
                    category="source",
                    label="METHOD",
                    node_id="Service.run",
                    properties={
                        "name": "run",
                        "file_path": "Service.java",
                        "start_line": 1,
                        "end_line": 1,
                        "code_text": "1 | void run() {}",
                        "summary": "실행한다",
                        "_search_embedding": [0.1, 0.2],
                    },
                ),
                ProductNode.build(
                    category="rule",
                    label="RULE",
                    node_id="Service.run::R-1",
                    properties={
                        "line": 1,
                        "code_text": "1 | void run() {}",
                        "effect_descriptions": ["실행한다"],
                    },
                ),
            ),
            relationships=(ProductRelationship.build(
                "HAS_RULE", "Service.run", "Service.run::R-1", {"order": 1},
            ),),
        )
        client = _Client()

        receipt = await replace_product_graph(client, graph)

        self.assertEqual(1, client.write_count)
        self.assertEqual(2, receipt.node_count)
        self.assertEqual(1, receipt.relationship_count)
        all_queries = "\n".join(
            [*client.schema_queries, *(query for query, _ in client.transaction.calls)]
        )
        self.assertIn("node._owner = $owner", all_queries)
        self.assertIn("relationship._owner = $owner", all_queries)
        self.assertIn("MATCH (source {_id: item.source_id})", all_queries)
        self.assertIn("relationship[item.from_column_key]", all_queries)
        self.assertIn("SET node = item", all_queries)


if __name__ == "__main__":
    unittest.main()
