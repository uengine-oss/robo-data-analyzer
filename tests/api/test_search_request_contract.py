"""REST search request parameters must reach the shared search implementation."""
from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from starlette.requests import Request

from api.search_router import SearchRequest, search_semantic
from search import service


class SearchRequestContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_rest_top_n_reaches_shared_service(self) -> None:
        captured: list[int | None] = []

        def fake_search(
            query, on_progress=None, *, top_n=None, analysis_scope_id=None,
        ):
            del analysis_scope_id
            captured.append(top_n)
            return [{"score": 1.0, "nodes": [{"id": "n1"}]}], {"ok": True}

        request = Request({"type": "http", "headers": []})
        with patch("api.search_router.run_search", side_effect=fake_search):
            response = await search_semantic(SearchRequest(query="inventory", top_n=3), request)
            records = [
                json.loads(chunk.decode("utf-8"))
                async for chunk in response.body_iterator
            ]

        self.assertEqual([3], captured)
        self.assertEqual("result", records[-2]["type"])
        self.assertEqual("stream_end", records[-1]["type"])

    async def test_service_uses_request_local_limit_without_mutating_default(self) -> None:
        observed: list[int] = []

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        class Driver:
            def session(self, database=None):
                return Session()

            def close(self):
                return None

        class Retriever:
            def __init__(self, _session, _corpus):
                pass

            def retrieve(self, _query, cfg, **_kwargs):
                observed.append(cfg.cluster_top_n)
                return []

        @contextmanager
        def scoped_session(_driver, _database, *, analysis_scope_id=None):
            del analysis_scope_id
            yield SimpleNamespace(
                analysis_scope_id="scope:1",
                generation_id="generation:1",
                quality_status="completed_with_findings",
                call_resolution={"total": 2, "unresolved": 1},
                import_resolution={"total": 3, "unresolved": 2},
            )

        default_limit = service.DEFAULT.cluster_top_n
        with (
            patch.object(
                service, "load_corpus",
                return_value=SimpleNamespace(
                    ids=[], analysis_scope_id="scope:1", generation_id="generation:1",
                ),
            ),
            patch.object(service, "read_driver", return_value=(Driver(), "neo4j")),
            patch.object(service, "Retriever", Retriever),
            patch.object(service, "completed_generation_session", scoped_session),
        ):
            _clusters, meta = service.run_search("inventory", top_n=3)

        self.assertEqual([3], observed)
        self.assertEqual(default_limit, service.DEFAULT.cluster_top_n)
        self.assertEqual("completed_with_findings", meta["quality_status"])
        self.assertEqual(1, meta["call_resolution"]["unresolved"])
        self.assertEqual(2, meta["import_resolution"]["unresolved"])

    def test_top_n_boundary_is_explicit(self) -> None:
        self.assertEqual(1, SearchRequest(query="q", top_n=1).top_n)
        self.assertEqual(100, SearchRequest(query="q", top_n=100).top_n)
        for invalid in (0, 101):
            with self.subTest(top_n=invalid), self.assertRaises(ValidationError):
                SearchRequest(query="q", top_n=invalid)


if __name__ == "__main__":
    unittest.main()
