from __future__ import annotations

import importlib
import importlib.util
import unittest


PUBLIC_MODULES = (
    "main",
    "api.analyze_router",
    "api.search_router",
    "api.source_router",
    "api.mcp_app",
    "pipeline.analysis_pipelines",
    "pipeline.execution.run_executor",
    "graph.neo4j_client",
    "search.service",
    "llm.client.llm_client",
)


@unittest.skipUnless(
    all(importlib.util.find_spec(root) is not None for root in ("pipeline", "graph", "llm")),
    "v10 migration is still in progress",
)
class PublicImportTest(unittest.TestCase):
    def test_final_public_modules_import(self) -> None:
        for module in PUBLIC_MODULES:
            with self.subTest(module=module):
                importlib.import_module(module)


if __name__ == "__main__":
    unittest.main()
