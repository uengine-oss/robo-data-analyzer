"""Analysis registry, destructive initialization and run identity contracts."""
from __future__ import annotations

import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pipeline.models import analysis_meta
from pipeline.models.analysis_context import ControlSubtreeNode, ReferencedSymbolContext
from pipeline.models.analysis_meta import AnalysisRun, Codebase
from pipeline.execution import run_executor, run_registry
from pipeline.execution.pause_controller import PauseController
from runtime_events.ndjson_event_stream import NDJSONStream


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
            first = AnalysisRun.now(codebase, Path("run-output"))
            second = AnalysisRun.now(codebase, Path("run-output"))

        self.assertNotEqual(first.run_id, second.run_id)

    def test_nested_analysis_context_types_are_checkpoint_allowlisted(self) -> None:
        self.assertIn(ReferencedSymbolContext, run_executor._STATE_MSGPACK_ALLOWLIST)
        self.assertIn(ControlSubtreeNode, run_executor._STATE_MSGPACK_ALLOWLIST)


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
