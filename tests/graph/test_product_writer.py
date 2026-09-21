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
            return _Result({"written": len(items) if items else 1})
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
        self.assertIn("MATCH (source:METHOD {product_id: $source_id})", all_queries)
        self.assertIn("MATCH (target:RULE {product_id: $target_id})", all_queries)
        self.assertIn("MATCH (node:METHOD {product_id: id, _owner: $owner})", all_queries)
        self.assertNotIn("MATCH (source {_id: item.source_id})", all_queries)
        self.assertIn("CREATE (source:METHOD)-[relationship:HAS_RULE", all_queries)
        self.assertIn("SET node = item.properties", all_queries)
        self.assertIn("{product_id: item.node_id}", all_queries)
        self.assertIn("node.product_id = item.node_id", all_queries)
        self.assertIn("`vector.dimensions`: 2", all_queries)
        node_batches = [
            parameters["items"]
            for query, parameters in client.transaction.calls
            if "RETURN count(node) AS written" in query
        ]
        for item in (item for batch in node_batches for item in batch):
            self.assertIn("node_id", item)
            self.assertNotIn("_id", item["properties"])

    async def test_inconsistent_embedding_dimensions_fail_before_write(self) -> None:
        graph = ProductGraph.build(
            nodes=(
                ProductNode.build(
                    category="source",
                    label="METHOD",
                    node_id="Service.first",
                    properties={
                        "code_text": "1 | void first() {}",
                        "_search_embedding": [0.1, 0.2],
                    },
                ),
                ProductNode.build(
                    category="source",
                    label="METHOD",
                    node_id="Service.second",
                    properties={
                        "code_text": "2 | void second() {}",
                        "_search_embedding": [0.1, 0.2, 0.3],
                    },
                ),
            ),
            relationships=(),
        )
        client = _Client()

        with self.assertRaisesRegex(Exception, "single dimension"):
            await replace_product_graph(client, graph)

        self.assertEqual(0, client.write_count)


if __name__ == "__main__":
    unittest.main()
