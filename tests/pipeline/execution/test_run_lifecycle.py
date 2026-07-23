"""Analysis registry, destructive initialization and run identity contracts."""
from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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

        def fail_reset(_path: Path) -> None:
            events.append("reset")
            raise OSError("reset unavailable")

        def record_clear(run_id: str) -> None:
            events.append("clear")
            original_clear(run_id)

        with (
            patch.object(run_executor, "set_active_run", side_effect=record_set_active),
            patch.object(run_executor, "_reset_run_output_root", side_effect=fail_reset),
            patch.object(run_executor, "clear_active_run", side_effect=record_clear),
        ):
            with self.assertRaisesRegex(OSError, "reset unavailable"):
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


if __name__ == "__main__":
    unittest.main()
