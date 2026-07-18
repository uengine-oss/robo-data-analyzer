"""Processing event context isolation and failure signaling contracts."""
from __future__ import annotations

import unittest

from runtime_events.processing_events import (
    emit_current,
    step_processing_scope,
    track_processing,
)


class _RecordingStream:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


class ProcessingEventContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_failure_have_exact_terminal_status(self) -> None:
        stream = _RecordingStream()
        with step_processing_scope("step.test", stream):  # type: ignore[arg-type]
            async with track_processing("CodeNode", "fn:ok"):
                pass
            with self.assertRaisesRegex(ValueError, "analysis failed"):
                async with track_processing("CodeNode", "fn:failed"):
                    raise ValueError("analysis failed")

        statuses = [event["status"] for event in stream.events]
        self.assertEqual(["start", "done", "start", "failed"], statuses)

    async def test_nested_scope_restores_outer_and_exit_clears_context(self) -> None:
        outer = _RecordingStream()
        inner = _RecordingStream()
        with step_processing_scope("outer", outer):  # type: ignore[arg-type]
            emit_current("Module", "outer-before")
            with step_processing_scope("inner", inner):  # type: ignore[arg-type]
                emit_current("Module", "inner")
            emit_current("Module", "outer-after")

        emit_current("Module", "outside")

        self.assertEqual(["outer-before", "outer-after"], [e["entity_id"] for e in outer.events])
        self.assertEqual(["inner"], [e["entity_id"] for e in inner.events])


if __name__ == "__main__":
    unittest.main()
