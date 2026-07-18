import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from starlette.requests import Request

import api.analyze_router as analyze_router
import api.search_router as search_router


def _request(*headers: tuple[bytes, bytes]) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": list(headers),
    })


async def _response_events(response) -> list[dict]:
    payload = b"".join([chunk async for chunk in response.body_iterator])
    return [json.loads(line) for line in payload.splitlines()]


class AnalyzerHttpBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_rejects_invalid_neo4j_override(self):
        request = _request((b"x-neo4j-uri", b"http://127.0.0.1:7687"))
        original_settings = analyze_router.SETTINGS
        analyze_router.SETTINGS = SimpleNamespace(
            analysis_llm=SimpleNamespace(api_key="configured")
        )
        try:
            with self.assertRaises(HTTPException) as raised:
                await analyze_router.analyze(
                    analyze_router.AnalyzeRequest(strategy="framework"), request
                )
        finally:
            analyze_router.SETTINGS = original_settings
        self.assertEqual(raised.exception.status_code, 400)

    async def test_analyze_ignores_legacy_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            (data_dir / "source").mkdir()
            (data_dir / "ddl").mkdir()
            captured = {}

            async def execute_run(*, strategy, codebase, stream):
                captured["codebase"] = codebase
                await stream.close()

            original = (
                analyze_router.SETTINGS,
                analyze_router.get_pipeline,
                analyze_router.execute_run,
            )
            analyze_router.SETTINGS = SimpleNamespace(
                analysis_llm=SimpleNamespace(api_key="configured"),
                analysis_data=SimpleNamespace(dir=data_dir),
            )
            analyze_router.get_pipeline = lambda name: SimpleNamespace(name=name)
            analyze_router.execute_run = execute_run
            try:
                response = await analyze_router.analyze(
                    analyze_router.AnalyzeRequest(
                        strategy="framework",
                        project_root="C:/untrusted/source",
                        ddl_dir="C:/untrusted/ddl",
                    ),
                    _request(),
                )
                await _response_events(response)
            finally:
                (
                    analyze_router.SETTINGS,
                    analyze_router.get_pipeline,
                    analyze_router.execute_run,
                ) = original

            self.assertEqual(captured["codebase"].root, data_dir / "source")
            self.assertEqual(captured["codebase"].ddl_dir, data_dir / "ddl")

    async def test_search_hides_unexpected_exception_details(self):
        def fail_search(*args, **kwargs):
            raise RuntimeError("secret connection detail")

        original = search_router.run_search
        search_router.run_search = fail_search
        try:
            response = await search_router.search_semantic(
                search_router.SearchRequest(query="orders"), _request()
            )
            events = await _response_events(response)
        finally:
            search_router.run_search = original

        error = next(event for event in events if event["type"] == "error")
        self.assertEqual(error["error_type"], "RuntimeError")
        self.assertNotIn("secret connection detail", error["message"])


if __name__ == "__main__":
    unittest.main()
