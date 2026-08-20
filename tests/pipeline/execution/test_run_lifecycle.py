"""Analysis registry, destructive initialization and run identity contracts."""
from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipeline.models import analysis_meta
from pipeline.models.analysis_context import ControlSubtreeNode, ReferencedSymbolContext
from pipeline.models.analysis_meta import AnalysisRun, Codebase
from pipeline.execution import run_executor, run_registry
from pipeline.execution.pause_controller import PauseController
from shared.events.ndjson_event_stream import NDJSONStream


class RunRegistryContractTest(unittest.TestCase):
    def setUp(self) -> None:
        run_registry._active = None

    def tearDown(self) -> None:
        run_registry._active = None

    def test_second_active_run_is_rejected_without_overwriting_first(self) -> None:
        first = PauseController()
        run_registry.set_active("run:first", first)

        with self.assertRaises(RuntimeError):
            run_registry.set_active("run:second", PauseController())

        self.assertEqual(("run:first", first), run_registry.get_active())

    def test_run_ids_are_unique_even_when_clock_value_is_identical(self) -> None:
        codebase = Codebase(root=Path("source"), strategy="framework")
        fixed_time = datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc)
        with patch.object(analysis_meta, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_time
            first = AnalysisRun.now(codebase, Path("output"))
            second = AnalysisRun.now(codebase, Path("output"))

        self.assertNotEqual(first.run_id, second.run_id)

    def test_nested_analysis_context_types_are_checkpoint_allowlisted(self) -> None:
        self.assertIn(ReferencedSymbolContext, run_executor._STATE_MSGPACK_ALLOWLIST)
        self.assertIn(ControlSubtreeNode, run_executor._STATE_MSGPACK_ALLOWLIST)

    def test_call_resolution_workflow_update_is_captured_exactly_once(self) -> None:
        row = {
            "call_id": "call:1", "ordinal": 0, "source_id": "sample.c",
            "caller_id": "code:sample.c:run", "owner_status": "resolved",
            "file": "sample.c", "line": 3,
            "range": {"char_offset": 10, "char_length": 5},
            "callee_range": {"char_offset": 10, "char_length": 3},
            "argument_ranges": [], "callee_kind": "named",
            "terminal_name": "run", "grammar_rule": "postfixExpression",
            "presence_status": "active", "presence": {
                "status": "active", "condition": None,
                "configurationId": None, "provenance": "unconditional",
            },
            "configured_preprocessing_required": False,
            "outcome": "edge", "reason": "", "scope": "internal",
            "callee_id": "code:sample.c:run",
            "preprocessing_evidence_ids": [], "macro_fact_ids": [],
        }

        ledger, seen = run_executor._capture_call_resolution_update(
            [], False, {"call_resolution_ledger": [row]},
        )

        self.assertTrue(seen)
        self.assertEqual([row], ledger)
        with self.assertRaisesRegex(ValueError, "more than one workflow producer"):
            run_executor._capture_call_resolution_update(
                ledger, seen, {"call_resolution_ledger": [row]},
            )

    def test_framework_requires_even_an_explicitly_empty_call_ledger(self) -> None:
        with self.assertRaisesRegex(ValueError, "did not produce"):
            run_executor._require_call_resolution_producer("framework", False)
        run_executor._require_call_resolution_producer("framework", True)
        run_executor._require_call_resolution_producer("dbms", False)


class UsageSummaryContractTest(unittest.TestCase):
    """캐시 적중은 호출로 세되 토큰이 0 이라, 집계가 둘을 구분해야 통계가 오해를 안 만든다."""

    def _summarize(self, records: tuple[dict, ...]) -> dict:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            (run_dir / "usage.jsonl").write_text(
                "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
                encoding="utf-8",
            )
            run_executor._summarize_usage(run_dir)
            return json.loads((run_dir / "usage_summary.json").read_text(encoding="utf-8"))

    def test_cache_hits_are_counted_separately_from_server_calls(self) -> None:
        summary = self._summarize((
            {"agent": "rule:a", "model": "m", "input_tokens": 10, "output_tokens": 5,
             "cached_tokens": 4, "total_tokens": 15, "cost_usd": 0.5,
             "response_cache_hit": False},
            {"agent": "rule:a", "model": "m", "input_tokens": 0, "output_tokens": 0,
             "cached_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
             "response_cache_hit": True},
        ))

        self.assertEqual(2, summary["total"]["calls"])
        self.assertEqual(1, summary["total"]["response_cache_hits"])
        self.assertEqual(15, summary["total"]["total_tokens"])
        self.assertEqual(4, summary["total"]["cached_tokens"],
                         "서버측 프리픽스 캐시 토큰이 응답 캐시 적중에 오염됐다")
        self.assertEqual(1, summary["per_agent_model"][0]["response_cache_hits"])

    def test_records_without_the_field_are_not_counted_as_hits(self) -> None:
        summary = self._summarize((
            {"agent": "rule:a", "model": "m", "input_tokens": 10, "output_tokens": 5,
             "cached_tokens": 0, "total_tokens": 15, "cost_usd": 0.5},
        ))

        self.assertEqual(1, summary["total"]["calls"])
        self.assertEqual(0, summary["total"]["response_cache_hits"])


class RunInitializationContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        run_registry._active = None

    async def asyncTearDown(self) -> None:
        run_registry._active = None

    async def test_registry_claim_precedes_reset_and_failed_reset_is_cleaned(self) -> None:
        events: list[str] = []
        stream = NDJSONStream()
        original_set_active = run_executor.set_active_run
        original_clear = run_executor.clear_active_run

        def record_set_active(run_id: str, controller: PauseController) -> None:
            events.append("set_active")
            original_set_active(run_id, controller)

        def fail_reset(
            _root: Path, _run_dir: Path | None = None, _strategy: object | None = None,
        ) -> None:
            events.append("reset")
            raise OSError("output unavailable")

        def record_clear(run_id: str) -> None:
            events.append("clear")
            original_clear(run_id)

        with (
            patch.object(run_executor, "set_active_run", side_effect=record_set_active),
            patch.object(run_executor, "_reset_run_output_root", side_effect=fail_reset),
            patch.object(run_executor, "clear_active_run", side_effect=record_clear),
        ):
            with self.assertRaisesRegex(OSError, "output unavailable"):
                await run_executor.execute_run(
                    strategy=object(),
                    codebase=Codebase(root=Path("source"), strategy="framework"),
                    stream=stream,
                )

        self.assertEqual(["set_active", "reset", "clear"], events)
        self.assertIsNone(run_registry.get_active())
        self.assertTrue(stream._closed)

    async def test_rejected_second_run_emits_explicit_error_before_stream_end(self) -> None:
        first = PauseController()
        run_registry.set_active("run:first", first)
        stream = NDJSONStream()

        with patch.object(run_executor, "_reset_run_output_root") as reset:
            with self.assertRaises(run_registry.ConcurrentAnalysisRunError):
                await run_executor.execute_run(
                    strategy=object(),
                    codebase=Codebase(root=Path("source"), strategy="framework"),
                    stream=stream,
                )

        reset.assert_not_called()
        records = [
            json.loads(chunk.decode("utf-8"))
            async for chunk in stream.iter()
        ]
        self.assertEqual(["error", "stream_end"], [record["type"] for record in records])
        self.assertIn("이미 활성", records[0]["message"])
        self.assertEqual(("run:first", first), run_registry.get_active())

    async def test_source_ast_closure_failure_precedes_neo4j_client(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            corpus = Path(temporary) / "corpus"
            source = corpus / "source"
            (corpus / "analysis").mkdir(parents=True)
            (corpus / "diagnostics").mkdir()
            source.mkdir()
            (source / "missing.c").write_text(
                "int f(void) { return 0; }\n", encoding="utf-8"
            )
            strategy = SimpleNamespace(name="framework", prompts_dir="llm/prompts/framework")

            with patch.object(run_executor, "Neo4jClient") as neo4j_client:
                with self.assertRaisesRegex(ValueError, "missing.c"):
                    await run_executor.execute_run(
                        strategy=strategy,
                        codebase=Codebase(
                            root=source,
                            strategy="framework",
                            corpus_root=corpus,
                        ),
                        run_output_root=Path(temporary) / "runs",
                    )

            neo4j_client.assert_not_called()

    async def test_pipeline_failure_quarantines_staging_without_publish(self) -> None:
        class Closure:
            artifacts: tuple = ()

            @staticmethod
            def as_dict() -> dict:
                return {"schema_version": "source-ast-closure/v1", "artifacts": []}

        class FailingApp:
            async def astream(self, *_args, **_kwargs):
                yield {"step1": {}}
                raise RuntimeError("injected pipeline failure")

        neo = SimpleNamespace(close=AsyncMock())
        ensure = AsyncMock()
        begin = AsyncMock()
        publish = AsyncMock()
        terminate = AsyncMock()
        strategy = SimpleNamespace(name="framework", prompts_dir="llm/prompts/framework")

        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(run_executor, "verify_source_ast_closure", return_value=Closure()),
                patch.object(
                    run_executor, "ast_and_source_roots",
                    return_value=(Path(temporary), Path(temporary)),
                ),
                patch.object(run_executor, "Neo4jClient", return_value=neo),
                patch.object(run_executor, "ensure_schema", new=ensure),
                patch.object(run_executor, "begin_generation", new=begin),
                patch.object(run_executor, "publish_generation", new=publish),
                patch.object(run_executor, "terminate_generation", new=terminate),
                patch.object(run_executor, "build_langgraph_workflow", return_value=FailingApp()),
                patch.object(run_executor.response_cache, "sweep"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected pipeline failure"):
                    await run_executor.execute_run(
                        strategy=strategy,
                        codebase=Codebase(
                            root=Path(temporary),
                            strategy="framework",
                            corpus_root=Path(temporary),
                        ),
                        run_output_root=Path(temporary) / "runs",
                    )

        ensure.assert_awaited_once_with(neo)
        begin.assert_awaited_once()
        publish.assert_not_awaited()
        terminate.assert_awaited_once()
        self.assertEqual("failed", terminate.await_args.kwargs["status"])
        neo.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
