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
from pipeline.models.call_resolution import (
    call_occurrence_node_id,
    call_resolution_storage_item,
)
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SemanticFrame,
    SemanticSlot,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    slot_id,
)


def _call_row(*, outcome="unresolved", reason="missing") -> dict:
    return {
        "call_id": "call-1", "ordinal": 0,
        "source_id": "source:a.c", "caller_id": "code:f",
        "owner_status": "resolved", "file": "a.c", "line": 3,
        "range": {"char_offset": 12, "char_length": 8},
        "callee_range": {"char_offset": 12, "char_length": 4},
        "argument_ranges": [], "callee_kind": "named", "terminal_name": "work",
        "grammar_rule": "postfixExpression", "presence_status": "active",
        "presence": {"status": "active", "condition": None,
                     "configurationId": None, "provenance": "unconditional"},
        "configured_preprocessing_required": False,
        "outcome": outcome, "reason": reason,
        "scope": None, "callee_id": "",
        "preprocessing_evidence_ids": [], "macro_fact_ids": [],
    }


def _semantic_frame() -> dict:
    target = StructuralTarget(
        id="code:f", type="FUNCTION", owner_id="code:file", file="f.c",
        start_line=1, end_line=4,
    )
    target_ref = evidence_id(target.id, "TARGET", 0)
    call_ref = evidence_id(target.id, "CALL", 3)
    profile = StructuralProfile(target=target, evidence={
        target_ref: EvidenceFact(
            id=target_ref, kind="TARGET", owner_id=target.id, file="f.c",
            start_line=1, end_line=4,
            attributes={
                "type": "FUNCTION", "signature": "", "name": "f",
                "params": "", "return_type": "",
            },
        ),
        call_ref: EvidenceFact(
            id=call_ref, kind="CALL", owner_id=target.id, file="f.c",
            start_line=3, end_line=3,
            attributes={"callee_id": "code:g", "callee_name": "g",
                        "scope": "external", "recursive": False,
                        "arguments_source": "", "result_target": ""},
        ),
    })
    responsibility = slot_id(target.id, "FUNCTION", "RESPONSIBILITY", "TARGET")
    frame = SemanticFrame(target=target, profile=profile, slots={
        responsibility: SemanticSlot(
            slot_id=responsibility, family="FUNCTION", role="RESPONSIBILITY",
            target_ref=target_ref, evidence_refs=(call_ref, target_ref),
            meaning="f의 책임", status="sufficient",
        ),
    })
    return frame.model_dump(mode="json", by_alias=True)


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
    def __init__(self, *, semantic_frame=None, call_occurrences=None):
        self.calls: list[str] = []
        self.semantic_frame = semantic_frame or _semantic_frame()
        self.call_occurrences = list(call_occurrences or [])

    async def run(self, query, **parameters):
        del parameters
        self.calls.append(query)
        if "RETURN count(g) AS matched" in query:
            return _AsyncResult(record={"matched": 1})
        if "n.semantic_frame_json AS semantic_frame_json" in query:
            rows = [
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
            ]
            rows.extend({
                "id": call_occurrence_node_id(row["call_id"]),
                "labels": ["ANALYZER_OWNED", "CALL_OCCURRENCE"],
                "semantic_frame_json": None,
            } for row in self.call_occurrences)
            return _AsyncResult(rows=rows)
        if "properties(r) AS properties" in query:
            rows = [{
                "source_id": "code:f",
                "rel_type": "READS",
                "target_id": "db.public.t",
                "properties": {
                    "generation_id": "run:new",
                    "evidence_line": 7,
                },
            }]
            rows.extend({
                "source_id": row["caller_id"],
                "rel_type": "HAS_CALL_OCCURRENCE",
                "target_id": call_occurrence_node_id(row["call_id"]),
                "properties": {
                    "generation_id": "run:new", "call_id": row["call_id"],
                    "ordinal": row["ordinal"],
                },
            } for row in self.call_occurrences if row["caller_id"])
            return _AsyncResult(rows=rows)
        if "CALL_OCCURRENCE" in query and "AS properties" in query:
            return _AsyncResult(rows=[{
                "properties": call_resolution_storage_item(row),
                "owner_ids": [row["caller_id"]] if row["caller_id"] else [],
            } for row in self.call_occurrences])
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
            call_resolution_ledger=[],
        )

        self.assertEqual(2, closure["nodes"]["count"])
        self.assertEqual(1, closure["edges"]["count"])
        self.assertEqual(1, closure["slots"]["count"])
        self.assertEqual("graph-generation-closure/v2", closure["schema_version"])
        self.assertEqual(0, closure["call_occurrences"]["count"])
        self.assertEqual(
            sorted(next(iter(_semantic_frame()["slots"].values()))["evidence_refs"]),
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

        with self.assertRaisesRegex(GraphGenerationError, "invalid semantic_frame_json"):
            await publish_generation(
                _Client(transaction),
                generation_id="run:new",
                quality_status="completed",
                call_resolution_ledger=[],
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
                call_resolution_ledger=[],
            )

        self.assertFalse(any(
            "active.current_generation_id" in query for query in transaction.calls
        ))

    async def test_unresolved_occurrence_upgrades_quality_and_closes_hash(self) -> None:
        ledger = [_call_row()]
        transaction = _PublishTransaction(call_occurrences=ledger)

        closure = await publish_generation(
            _Client(transaction),
            generation_id="run:new",
            quality_status="completed",
            call_resolution_ledger=ledger,
        )

        self.assertEqual("completed_with_findings", closure["quality_status"])
        self.assertEqual(1, closure["call_occurrences"]["count"])
        self.assertEqual({"unresolved": 1}, closure["call_occurrences"]["by_outcome"])
        self.assertEqual({"missing": 1}, closure["call_occurrences"]["by_reason"])

    async def test_occurrence_db_state_mismatch_never_reaches_pointer(self) -> None:
        transaction = _PublishTransaction(call_occurrences=[])
        with self.assertRaisesRegex(GraphGenerationError, "call occurrence ledger"):
            await publish_generation(
                _Client(transaction),
                generation_id="run:new",
                quality_status="completed_with_findings",
                call_resolution_ledger=[_call_row()],
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
    @staticmethod
    def _record() -> dict:
        return {
            "generation_id": "run:published",
            "status": "completed",
            "call_resolution_summary_json": json.dumps({
                "total": 0, "unresolved": 0,
                "by_outcome": {}, "by_reason": {}, "sha256": "0" * 64,
            }),
        }

    def test_scoped_session_injects_resolved_generation_into_every_read(self) -> None:
        driver = _SyncDriver(self._record())

        with completed_generation_session(driver, "neo4j") as session:
            session.run("MATCH (n {generation_id:$generation_id}) RETURN n")
            self.assertEqual("completed", session.quality_status)
            self.assertEqual(0, session.call_resolution["total"])

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
        driver = _SyncDriver(self._record())

        with completed_generation_session(driver, "neo4j") as session:
            with self.assertRaises(CompletedGenerationUnavailable):
                session.run("RETURN $generation_id", generation_id="run:other")

    def test_generation_without_call_resolution_closure_fails_closed(self) -> None:
        driver = _SyncDriver({"generation_id": "run:published", "status": "completed"})
        with self.assertRaisesRegex(CompletedGenerationUnavailable, "call resolution"):
            with completed_generation_session(driver, "neo4j"):
                pass


if __name__ == "__main__":
    unittest.main()
