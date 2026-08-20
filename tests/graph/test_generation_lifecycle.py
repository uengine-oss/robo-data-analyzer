"""Behavioral tests for atomic graph-generation publication and quarantine."""
from __future__ import annotations

import json
import unittest

from graph.generation import (
    GraphGenerationError,
    publish_generation,
    terminate_generation,
)
from search.corpus.generation import (
    CompletedGenerationUnavailable,
    completed_generation_session,
)


class _AsyncResult:
    def __init__(self, *, record=None, rows=None):
        self.record = record
        self.rows = list(rows or [])
        self.consumed = False

    async def single(self, strict=False):
        del strict
        return self.record

    async def data(self):
        return list(self.rows)

    async def consume(self):
        self.consumed = True
        return None


class _PublishTransaction:
    def __init__(self, *, semantic_frame=None):
        self.calls: list[str] = []
        self.semantic_frame = semantic_frame or {
            "claims": [{
                "slotId": "SL:responsibility",
                "type": "FUNCTION.RESPONSIBILITY",
                "status": "sufficient",
                "evidenceRefs": ["EV:2", "EV:1"],
            }]
        }

    async def run(self, query, **parameters):
        del parameters
        self.calls.append(query)
        if "RETURN count(g) AS matched" in query:
            return _AsyncResult(record={"matched": 1})
        if "n.semantic_frame_json AS semantic_frame_json" in query:
            return _AsyncResult(rows=[
                {
                    "id": "code:f",
                    "labels": ["FUNCTION", "ANALYZER_OWNED"],
                    "semantic_frame_json": json.dumps(self.semantic_frame),
                },
                {
                    "id": "db.public.t",
                    "labels": ["ANALYZER_OWNED", "TABLE"],
                    "semantic_frame_json": None,
                },
            ])
        if "properties(r) AS properties" in query:
            return _AsyncResult(rows=[{
                "source_id": "code:f",
                "rel_type": "READS",
                "target_id": "db.public.t",
                "properties": {
                    "generation_id": "run:new",
                    "evidence_line": 7,
                },
            }])
        if "active.current_generation_id AS active_generation_id" in query:
            return _AsyncResult(record={
                "run_id": "run:new",
                "active_generation_id": "run:new",
            })
        if "RETURN count(n) AS migrated" in query:
            return _AsyncResult()
        if "DETACH DELETE n" in query:
            return _AsyncResult()
        raise AssertionError(query)


class _TerminateTransaction:
    def __init__(self):
        self.calls: list[str] = []

    async def run(self, query, **parameters):
        del parameters
        self.calls.append(query)
        if "RETURN count(g) AS matched" in query:
            return _AsyncResult(record={"matched": 1})
        if "DETACH DELETE n" in query:
            return _AsyncResult()
        raise AssertionError(query)


class _Client:
    def __init__(self, transaction):
        self.generation_id = "run:new"
        self.transaction = transaction

    async def execute_write(self, callback):
        return await callback(self.transaction)


class GraphGenerationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_closes_identities_then_flips_pointer_then_cleans_old(self) -> None:
        transaction = _PublishTransaction()

        closure = await publish_generation(
            _Client(transaction),
            generation_id="run:new",
            quality_status="completed",
        )

        self.assertEqual(2, closure["nodes"]["count"])
        self.assertEqual(1, closure["edges"]["count"])
        self.assertEqual(1, closure["slots"]["count"])
        self.assertEqual(
            ["EV:1", "EV:2"],
            closure["slots"]["identities"][0]["evidence_refs"],
        )
        publish_index = next(
            index for index, query in enumerate(transaction.calls)
            if "active.current_generation_id AS active_generation_id" in query
        )
        cleanup_index = next(
            index for index, query in enumerate(transaction.calls)
            if "DETACH DELETE n" in query
        )
        self.assertLess(publish_index, cleanup_index)

    async def test_invalid_slot_closure_never_reaches_pointer_publish(self) -> None:
        transaction = _PublishTransaction(semantic_frame={"claims": [{"type": "x"}]})

        with self.assertRaisesRegex(GraphGenerationError, "slot id"):
            await publish_generation(
                _Client(transaction),
                generation_id="run:new",
                quality_status="completed",
            )

        self.assertFalse(any(
            "active.current_generation_id" in query for query in transaction.calls
        ))

    async def test_edge_without_matching_generation_never_reaches_pointer_publish(self) -> None:
        transaction = _PublishTransaction()
        original_run = transaction.run

        async def run_with_bad_edge(query, **parameters):
            result = await original_run(query, **parameters)
            if "properties(r) AS properties" in query:
                result.rows[0]["properties"].pop("generation_id")
            return result

        transaction.run = run_with_bad_edge
        with self.assertRaisesRegex(GraphGenerationError, "generation id"):
            await publish_generation(
                _Client(transaction),
                generation_id="run:new",
                quality_status="completed",
            )

        self.assertFalse(any(
            "active.current_generation_id" in query for query in transaction.calls
        ))

    async def test_failure_terminal_status_and_staging_delete_share_transaction(self) -> None:
        transaction = _TerminateTransaction()

        await terminate_generation(
            _Client(transaction),
            generation_id="run:new",
            status="failed",
            reason="injected failure",
        )

        self.assertIn("SET g.status = $status", transaction.calls[0])
        self.assertIn("DETACH DELETE n", transaction.calls[1])


class _SyncResult:
    def __init__(self, record):
        self.record = record

    def single(self, strict=False):
        del strict
        return self.record

    def data(self):
        return [] if self.record is None else [self.record]


class _SyncSession:
    def __init__(self, record):
        self.record = record
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        return _SyncResult(self.record)


class _SyncDriver:
    def __init__(self, record):
        self.session_value = _SyncSession(record)

    def session(self, database=None):
        del database
        return self.session_value


class CompletedGenerationReaderTests(unittest.TestCase):
    def test_scoped_session_injects_resolved_generation_into_every_read(self) -> None:
        driver = _SyncDriver({"generation_id": "run:published", "status": "completed"})

        with completed_generation_session(driver, "neo4j") as session:
            session.run("MATCH (n {generation_id:$generation_id}) RETURN n")

        self.assertEqual(
            "run:published",
            driver.session_value.calls[-1][1]["generation_id"],
        )

    def test_missing_active_completed_pointer_fails_closed(self) -> None:
        driver = _SyncDriver(None)
        with self.assertRaises(CompletedGenerationUnavailable):
            with completed_generation_session(driver, "neo4j"):
                pass

    def test_scoped_session_rejects_generation_override(self) -> None:
        driver = _SyncDriver({"generation_id": "run:published", "status": "completed"})

        with completed_generation_session(driver, "neo4j") as session:
            with self.assertRaises(CompletedGenerationUnavailable):
                session.run("RETURN $generation_id", generation_id="run:other")


if __name__ == "__main__":
    unittest.main()
