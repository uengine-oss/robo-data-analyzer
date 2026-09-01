from __future__ import annotations

import importlib
import unittest


PUBLIC_MODULES = (
    "main",
    "shared.config.app_settings",
    "shared.events.runtime_logger",
    "shared.events.ndjson_event_stream",
    "shared.events.processing_events",
    "api.analyze_router",
    "api.search_router",
    "api.mcp_app",
    "pipeline.pipeline_strategies",
    "pipeline.execution.run_executor",
    "graph.neo4j_client",
    "search.search_service",
    "llm.llm_client",
)


class PublicImportTest(unittest.TestCase):
    def test_public_modules_import(self) -> None:
        for module in PUBLIC_MODULES:
            with self.subTest(module=module):
                importlib.import_module(module)


if __name__ == "__main__":
    unittest.main()
