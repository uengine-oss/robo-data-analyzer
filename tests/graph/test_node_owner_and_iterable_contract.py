"""Analyzer node ownership and one-shot iterable regression tests."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from graph.nodes.module import upsert_modules
from graph.schema.graph_nodes import Module


GRAPH_DIRECTORY = Path(__file__).resolve().parents[2] / "graph"


class NodeOwnerAndIterableContractTest(unittest.IsolatedAsyncioTestCase):
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
