"""NDJSON consumer and producer task must share one lifecycle."""
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.events import ndjson_event_stream
from shared.events.ndjson_event_stream import ASYNC_EVENT_HIGH_WATER_MARK, NDJSONStream


class NDJSONEventStreamLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_bulk_emit_backpressures_without_losing_order(self) -> None:
        stream = NDJSONStream()
        event_count = ASYNC_EVENT_HIGH_WATER_MARK * 2

        async def produce() -> None:
            for sequence in range(event_count):
                await stream.emit_async({"type": "node_event", "sequence": sequence})
            await stream.close()

        with patch.object(ndjson_event_stream, "_mirror_event_to_logger"):
            producer = asyncio.create_task(produce())
            for _ in range(10):
                await asyncio.sleep(0)

            self.assertFalse(producer.done())
            self.assertEqual(stream.queued_event_count, ASYNC_EVENT_HIGH_WATER_MARK)

            records = [json.loads(chunk) async for chunk in stream.iter()]
            await producer

        self.assertEqual(
            [record["sequence"] for record in records[:-1]],
            list(range(event_count)),
        )
        self.assertEqual(records[-1]["type"], "stream_end")
        self.assertLessEqual(stream.peak_queued_event_count, ASYNC_EVENT_HIGH_WATER_MARK)

    def test_product_message_is_mirrored_to_runtime_log(self) -> None:
        stream = NDJSONStream()
        with patch.object(ndjson_event_stream, "log_info") as log_info:
            stream.emit({"type": "message", "content": "DDL parsing complete"})

        log_info.assert_called_once_with(
            "stream.message",
            "message",
            content="DDL parsing complete",
        )

    def test_error_origin_component_does_not_collide_with_logger_component(self) -> None:
        stream = NDJSONStream()
        with patch.object(ndjson_event_stream, "log_info") as log_info:
            stream.emit({
                "type": "error",
                "component": "pipeline.step",
                "operation": "failed",
                "message": "original error",
            })

        log_info.assert_called_once_with(
            "stream.error",
            "failed",
            origin_component="pipeline.step",
            message="original error",
        )

    def test_analyze_and_search_routes_keep_the_producer_lifecycle(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        for relative_path in ("api/analyze_router.py", "api/search_router.py"):
            source = (repository_root / relative_path).read_text(encoding="utf-8")
            with self.subTest(route=relative_path):
                self.assertIn("stream.iter_with_producer(producer)", source)
                self.assertNotIn("StreamingResponse(stream.iter()", source)

    async def test_normal_stream_end_collects_the_producer(self) -> None:
        stream = NDJSONStream()

        async def produce() -> None:
            stream.emit({"type": "result", "value": 1})
            await stream.close()

        producer = asyncio.create_task(produce())
        records = [
            json.loads(chunk.decode("utf-8"))
            async for chunk in stream.iter_with_producer(producer)
        ]

        self.assertEqual(["result", "stream_end"], [record["type"] for record in records])
        self.assertTrue(producer.done())
        self.assertFalse(producer.cancelled())
        self.assertIsNone(producer.exception())

    async def test_consumer_close_cancels_and_collects_the_producer(self) -> None:
        stream = NDJSONStream()
        producer_started = asyncio.Event()
        producer_cleaned = asyncio.Event()
        never_complete = asyncio.Event()

        async def produce() -> None:
            try:
                stream.emit({"type": "phase_event", "event": "start"})
                producer_started.set()
                await never_complete.wait()
            finally:
                producer_cleaned.set()
                await stream.close()

        producer = asyncio.create_task(produce())
        response_iterator = stream.iter_with_producer(producer)
        first = json.loads((await anext(response_iterator)).decode("utf-8"))
        self.assertEqual("phase_event", first["type"])
        await producer_started.wait()

        await response_iterator.aclose()

        self.assertTrue(producer_cleaned.is_set())
        self.assertTrue(producer.done())
        self.assertTrue(producer.cancelled())

    async def test_full_queue_disconnect_does_not_deadlock_producer_cleanup(self) -> None:
        stream = NDJSONStream()
        producer_cleaned = asyncio.Event()

        async def produce() -> None:
            try:
                for sequence in range(ASYNC_EVENT_HIGH_WATER_MARK * 2):
                    await stream.emit_async({"type": "node_event", "sequence": sequence})
            finally:
                producer_cleaned.set()
                await stream.close()

        with patch.object(ndjson_event_stream, "_mirror_event_to_logger"):
            producer = asyncio.create_task(produce())
            response_iterator = stream.iter_with_producer(producer)
            first = json.loads((await anext(response_iterator)).decode("utf-8"))
            self.assertEqual(0, first["sequence"])

            for _ in range(10):
                await asyncio.sleep(0)
            self.assertFalse(producer.done())

            await asyncio.wait_for(response_iterator.aclose(), timeout=1)

        self.assertTrue(producer_cleaned.is_set())
        self.assertTrue(producer.done())
        self.assertTrue(producer.cancelled())


if __name__ == "__main__":
    unittest.main()
