"""Analyzer node ownership and one-shot iterable regression tests."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from graph.nodes.module import to_item as module_to_item, upsert_modules
from graph.nodes.code_node import update_summaries
from graph.nodes.stereotype_label import apply_stereotype_labels
from graph.schema.graph_nodes import CodeNode, Module


GRAPH_DIRECTORY = Path(__file__).resolve().parents[2] / "graph"


class NodeOwnerAndIterableContractTest(unittest.IsolatedAsyncioTestCase):
    def test_empty_module_derived_meaning_clears_stale_graph_properties(self) -> None:
        module = Module(
            id="code:orders.c",
            name="orders.c",
            file_path="orders.c",
            label="FILE",
        )

        item = module_to_item(module)

        self.assertIsNone(item["summary"])
        self.assertIsNone(item["stereotype"])
        self.assertIsNone(item["logical_name"])
        self.assertIsNone(item["semantic_frame_schema"])
        self.assertIsNone(item["semantic_frame_json"])

    async def test_stereotype_replacement_clears_stale_labels_even_for_empty_role(self) -> None:
        client = AsyncMock()

        await apply_stereotype_labels(
            client, [("a", "Query"), ("b", ""), ("a", "Command")]
        )

        self.assertEqual(2, client.run_batch_unwind.await_count)
        set_query, set_items = client.run_batch_unwind.await_args_list[0].args
        self.assertIn("REMOVE", set_query)
        self.assertIn("SET node:`COMMAND`", set_query)
        self.assertNotIn("SET node:`QUERY`", set_query)
        self.assertEqual([{"id": "a"}], set_items)
        clear_query, clear_items = client.run_batch_unwind.await_args_list[1].args
        self.assertIn("REMOVE", clear_query)
        self.assertNotIn("SET node:", clear_query)
        self.assertEqual([{"id": "b"}], clear_items)

    async def test_insufficient_summary_clears_stale_summary_and_role(self) -> None:
        node = CodeNode(
            id="code:insufficient",
            signature="f()",
            file_path="sample.py",
            start_line=1,
            end_line=1,
            label="FUNCTION",
            extra={
                "summary_evidence_status": "insufficient",
                "summary_missing_context": ["runtime dispatch target"],
            },
        )
        client = AsyncMock()

        await update_summaries(client, [node])

        self.assertEqual(2, client.run_batch_unwind.await_count)
        summary_query, summary_items = client.run_batch_unwind.await_args_list[0].args
        self.assertIn("REMOVE node.domain_terms", summary_query)
        self.assertIn("node.summary_evidence_status", summary_query)
        self.assertEqual("insufficient", summary_items[0]["summary_evidence_status"])
        self.assertNotIn("summary", summary_items[0])
        clear_query, clear_items = client.run_batch_unwind.await_args_list[1].args
        self.assertIn("REMOVE", clear_query)
        self.assertEqual([{"id": node.id}], clear_items)

    def test_node_identity_queries_include_analyzer_owner(self) -> None:
        violations: list[str] = []
        candidates = [GRAPH_DIRECTORY / "node_upsert.py", *sorted((GRAPH_DIRECTORY / "nodes").glob("*.py"))]
        for candidate in candidates:
            source = candidate.read_text(encoding="utf-8")
            for line_number, line in enumerate(source.splitlines(), start=1):
                stripped = line.strip()
                if "{id: item.id}" in stripped and stripped.startswith(("MERGE (", "MATCH (")):
                    violations.append(f"{candidate.name}:{line_number}: {stripped}")

        self.assertEqual(violations, [], "node identity query omitted graph_owner")

    async def test_module_generator_is_reused_for_storage_and_stereotype_label(self) -> None:
        module = Module(
            id="code:orders.c",
            name="orders.c",
            file_path="orders.c",
            label="FILE",
            stereotype="Service",
        )
        graph_database = object()
        store_nodes = AsyncMock()
        apply_labels = AsyncMock()

        with (
            patch("graph.nodes.module.upsert_by_node_label", store_nodes),
            patch("graph.nodes.module.apply_stereotype_labels", apply_labels),
        ):
            await upsert_modules(graph_database, (item for item in [module]))

        stored_modules = store_nodes.await_args.args[1]
        self.assertEqual(stored_modules, [module])
        self.assertEqual(apply_labels.await_args.args[1], [(module.id, "Service")])


if __name__ == "__main__":
    unittest.main()
