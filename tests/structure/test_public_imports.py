from __future__ import annotations

import importlib
import importlib.util
import unittest
from unittest.mock import patch


PUBLIC_MODULES = (
    "main",
    "analyzer_settings",
    "runtime_events.runtime_logger",
    "runtime_events.ndjson_event_stream",
    "runtime_events.processing_events",
    "api.analyze_router",
    "api.search_router",
    "api.source_router",
    "api.mcp_app",
    "pipeline.strategies",
    "pipeline.execution.run_executor",
    "graph.neo4j_client",
    "search.service",
    "search.query.rewrite",
    "search.query.judge",
    "llm.llm_client",
)


def _all_public_modules_exist() -> bool:
    """부분 마이그레이션 중에는 최종 import 게이트를 열지 않는다."""
    for module in PUBLIC_MODULES:
        try:
            if importlib.util.find_spec(module) is None:
                return False
        except ModuleNotFoundError:
            return False
    return True


@unittest.skipUnless(
    _all_public_modules_exist(),
    "v10 migration is still in progress",
)
class PublicImportTest(unittest.TestCase):
    def test_final_public_modules_import(self) -> None:
        for module in PUBLIC_MODULES:
            with self.subTest(module=module):
                importlib.import_module(module)

    def test_query_llm_reads_search_settings_contract(self) -> None:
        from search.query import query_llm
        from analyzer_settings import SETTINGS

        query_llm._cfg_cache.clear()
        sentinel_config = object()
        try:
            with patch.object(query_llm, "load_llm_config", return_value=sentinel_config) as loader:
                config, api_key = query_llm._config()
            loader.assert_called_once_with(SETTINGS.semantic_search.llm_config)
            self.assertIs(config, sentinel_config)
            self.assertEqual(api_key, SETTINGS.semantic_search.llm_api_key or SETTINGS.analysis_llm.api_key)
        finally:
            query_llm._cfg_cache.clear()


if __name__ == "__main__":
    unittest.main()
