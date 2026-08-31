"""Behavioral tests for atomic graph-generation publication and quarantine."""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from graph.generation import (
    GraphGenerationError,
    _canonical_json,
    _control_hash_ledger,
    _generation_control_hash,
    _scope_control_hash,
    begin_generation,
    publish_generation,
    terminate_generation,
)
from graph.product_batch_writer import ProductPlanVerificationReceipt
from graph.product_write_batch import (
    ProductWriteContractError,
    ProductWriteBatch,
    combine_product_write_batches,
)
from graph.publication_schema import PRODUCT_STAGE_NAMES
from pipeline.publication.analysis_health import build_analysis_health_write_batch
from search.corpus.generation import (
    CompletedGenerationUnavailable,
    completed_generation_session,
)

SCOPE = "analysis-scope/v1:" + "a" * 64


def _product_plan(*, unresolved_calls=0, unresolved_data_objects=0):
    batches = []
    for stage_name in sorted(PRODUCT_STAGE_NAMES - {"analysis_health"}):
        batches.append(ProductWriteBatch.build(
            stage_name=stage_name,
            analysis_scope_id=SCOPE,
            generation_id="run:new",
        ))
    batches.append(build_analysis_health_write_batch(
        analysis_scope_id=SCOPE,
        generation_id="run:new",
        missing_total=0,
        duplicate_total=0,
        unresolved_call_count=unresolved_calls,
        unresolved_data_object_count=unresolved_data_objects,
        prepublish_wall_time_ms=123,
        prepublish_performance_artifact_hash="b" * 64,
    ))
    return combine_product_write_batches(
        batches,
        analysis_scope_id=SCOPE,
        generation_id="run:new",
    )


def _verification_receipt(plan) -> ProductPlanVerificationReceipt:
    return ProductPlanVerificationReceipt(
        stage_hash_by_name=tuple(sorted(
            (batch.stage_name, batch.canonical_batch_sha256)
            for batch in plan.batches
        )),
        operation_count=len(plan.required_operation_key_set),
        record_hash_by_identity=plan.required_product_record_hash_by_identity,
    )


def _call_row(*, outcome="unresolved", reason="missing") -> dict:
    return {
        "call_id": "call-1", "ordinal": 0,
        "caller_id": "code:f",
        "resolution_status": "unsupported",
        "adapter_schema": "language-neutral-resolution/v1",
        "outcome": outcome, "reason": reason,
        "scope": None, "callee_id": "",
        "external_identity_key": "", "declaration_fact_id": "",
        "candidate_fact_ids": [],
        "preprocessing_evidence_ids": [], "macro_fact_ids": [],
    }


def _import_row() -> dict:
    import_id = "a" * 64
    return {
        "occurrence_id": f"{import_id}:0", "import_id": import_id,
        "ordinal": 0, "entry_ordinal": 0, "evidence_version": "1.1.0",
        "source_id": "a.c", "line": 1, "end_line": 1,
        "range": {"char_offset": 0, "char_length": 20},
        "directive_kind": "include", "grammar_rule": "includeLine",
        "presence_status": "active",
        "evidence_status": "complete", "import_kind": "source_file",
        "target_kind": "quoted", "target": '"common.h"',
        "target_range": {"char_offset": 9, "char_length": 10},
        "path_components": ["common.h"],
        "path_component_ranges": [{"char_offset": 10, "char_length": 8}],
        "member": None, "member_range": None, "alias": None,
        "alias_range": None, "relative_level": 0, "wildcard": False,
        "locality": "local", "outcome": "unresolved",
        "reason": "insufficient_import_binding_environment",
        "internal_module_ids": [],
    }


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
    def __init__(
        self, *, cas_conflict=False, active_count=1,
        tamper_locked_control_hash=False,
    ):
        self.calls: list[str] = []
        self.parameters: list[dict] = []
        self.cas_conflict = cas_conflict
        self.active_count = active_count
        self.tamper_locked_control_hash = tamper_locked_control_hash
        self.previous_active_generation_id = "run:old"
        self.publication_version = 8
        self.previous_expected_publication_version = 5
        self.previous_expected_active_generation_id = "run:older"
        self.verified_locked_control_hashes_json: str | None = None
        self.expected_post_control_hashes_json: str | None = None
        self.product_plan_sha256: str | None = None
        self.product_stage_count: int | None = None
        self.product_operation_count: int | None = None
        self.product_record_count: int | None = None
        self.published_quality_status: str | None = None

    def _initial_control_hashes_json(self) -> str:
        return _canonical_json(_control_hash_ledger(
            _scope_control_hash(
                SCOPE,
                publication_version=self.publication_version - 1,
                active_generation_id=self.previous_active_generation_id,
            ),
            _generation_control_hash(
                SCOPE,
                self.previous_active_generation_id,
                generation_status="completed",
                publication_status="active",
                expected_publication_version=(
                    self.previous_expected_publication_version
                ),
                expected_active_generation_id=(
                    self.previous_expected_active_generation_id
                ),
            ),
        ))

    def _locked_control_hashes_json(self) -> str:
        return _canonical_json(_control_hash_ledger(
            _scope_control_hash(
                SCOPE,
                publication_version=self.publication_version,
                active_generation_id=self.previous_active_generation_id,
            ),
            _generation_control_hash(
                SCOPE,
                self.previous_active_generation_id,
                generation_status="completed",
                publication_status="active",
                expected_publication_version=(
                    self.previous_expected_publication_version
                ),
                expected_active_generation_id=(
                    self.previous_expected_active_generation_id
                ),
            ),
            _generation_control_hash(
                SCOPE,
                "run:new",
                generation_status="building",
                publication_status="building",
                expected_publication_version=self.publication_version - 1,
                expected_active_generation_id=self.previous_active_generation_id,
            ),
        ))

    async def run(self, query, **parameters):
        self.calls.append(query)
        self.parameters.append(dict(parameters))
        if "locked_publication_version" in query and "SET scope.publication_version" in query:
            if self.cas_conflict:
                return _AsyncResult(rows=[])
            return _AsyncResult(rows=[{
                "generation_id": "run:new",
                "generation_status": "building",
                "publication_status": "building",
                "expected_publication_version": self.publication_version - 1,
                "previous_active_generation_id": self.previous_active_generation_id,
                "initial_control_record_hashes_json": (
                    self._initial_control_hashes_json()
                ),
                "expected_locked_control_pretransition_hashes_json": (
                    _canonical_json({"tampered": "0" * 64})
                    if self.tamper_locked_control_hash
                    else self._locked_control_hashes_json()
                ),
                "locked_active_generation_id": self.previous_active_generation_id,
                "locked_publication_version": self.publication_version,
                "previous_generation_status": "completed",
                "previous_publication_status": "active",
                "previous_expected_publication_version": (
                    self.previous_expected_publication_version
                ),
                "previous_expected_active_generation_id": (
                    self.previous_expected_active_generation_id
                ),
            }])
        if "RETURN count(g) AS matched" in query:
            return _AsyncResult(record={"matched": 1})
        if "scope.active_generation_id = $generation_id" in query:
            self.verified_locked_control_hashes_json = parameters[
                "verified_locked_control_pretransition_hashes_json"
            ]
            self.expected_post_control_hashes_json = parameters[
                "expected_post_control_record_hashes_json"
            ]
            self.product_plan_sha256 = parameters["product_plan_sha256"]
            self.product_stage_count = parameters["product_stage_count"]
            self.product_operation_count = parameters["product_operation_count"]
            self.product_record_count = parameters["product_record_count"]
            self.published_quality_status = parameters["quality_status"]
            return _AsyncResult(record={
                "generation_id": "run:new",
                "active_generation_id": "run:new",
                "publication_version": self.publication_version,
            })
        if "current_quality_status" in query:
            return _AsyncResult(record={
                "active_generation_id": "run:new",
                "publication_version": self.publication_version,
                "current_quality_status": self.published_quality_status,
                "current_publication_status": "active",
                "current_expected_publication_version": self.publication_version - 1,
                "current_expected_active_generation_id": (
                    self.previous_active_generation_id
                ),
                "current_product_plan_sha256": self.product_plan_sha256,
                "current_product_stage_count": self.product_stage_count,
                "current_product_operation_count": self.product_operation_count,
                "current_product_record_count": self.product_record_count,
                "verified_locked_control_pretransition_hashes_json": parameters.get(
                    "verified_locked_control_pretransition_hashes_json",
                    self.verified_locked_control_hashes_json,
                ),
                "expected_post_control_record_hashes_json": parameters.get(
                    "expected_post_control_record_hashes_json",
                    self.expected_post_control_hashes_json,
                ),
                "previous_generation_status": "completed",
                "previous_publication_status": "superseded",
                "previous_expected_publication_version": (
                    self.previous_expected_publication_version
                ),
                "previous_expected_active_generation_id": (
                    self.previous_expected_active_generation_id
                ),
            })
        if "size(active_generations) AS active_count" in query:
            active_generation_ids = ["run:new"]
            if self.active_count != 1:
                active_generation_ids.append("run:unexpected")
            return _AsyncResult(record={
                "active_count": self.active_count,
                "active_generation_ids": active_generation_ids,
                "verified_post_control_record_hashes_json": parameters.get(
                    "verified_post_control_record_hashes_json"
                ),
            })
        if "DETACH DELETE n" in query:
            return _AsyncResult()
        raise AssertionError(query)


class _BeginTransaction:
    def __init__(self):
        self.calls: list[str] = []

    async def run(self, query, **parameters):
        self.calls.append(query)
        if "previous_generation_status" in query:
            return _AsyncResult(record={
                "publication_version": 7,
                "active_generation_id": "run:old",
                "previous_generation_status": "completed",
                "previous_publication_status": "active",
                "previous_expected_publication_version": 5,
                "previous_expected_active_generation_id": "run:older",
            })
        return _AsyncResult(record={
            "generation_id": parameters["generation_id"],
            "generation_status": "building",
            "publication_status": "building",
            "expected_publication_version": parameters[
                "expected_publication_version"
            ],
            "expected_active_generation_id": parameters[
                "expected_active_generation_id"
            ],
            "initial_control_record_hashes_json": parameters[
                "initial_control_record_hashes_json"
            ],
            "expected_locked_control_pretransition_hashes_json": parameters[
                "expected_locked_control_pretransition_hashes_json"
            ],
            "building_control_record_sha256": parameters[
                "building_control_record_sha256"
            ],
        })


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
        self.analysis_scope_id = SCOPE
        self.transaction = transaction

    async def execute_write(self, callback):
        return await callback(self.transaction)


class GraphGenerationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _publish(
        self,
        transaction,
        *,
        call_resolution_ledger=None,
        import_resolution_ledger=None,
        quality_status="completed",
        unresolved_data_objects=0,
        verification_error: Exception | None = None,
    ):
        call_ledger = list(call_resolution_ledger or [])
        plan = _product_plan(
            unresolved_calls=sum(
                row.get("outcome") == "unresolved" for row in call_ledger
            ),
            unresolved_data_objects=unresolved_data_objects,
        )
        verifier = AsyncMock(
            side_effect=verification_error,
            return_value=(
                None if verification_error else _verification_receipt(plan)
            ),
        )
        with patch(
            "graph.generation.verify_product_plan_before_publication",
            new=verifier,
        ):
            result = await publish_generation(
                _Client(transaction),
                generation_id="run:new",
                quality_status=quality_status,
                call_resolution_ledger=call_ledger,
                import_resolution_ledger=list(import_resolution_ledger or []),
                product_plan=plan,
            )
        return result, verifier

    async def test_begin_generation_seals_scope_version_and_active_pointer(self) -> None:
        transaction = _BeginTransaction()

        await begin_generation(
            _Client(transaction), generation_id="run:new", strategy="framework",
            source_ast_closure={"sha256": "a" * 64},
        )

        self.assertEqual(2, len(transaction.calls))
        self.assertIn("scope.publication_version", transaction.calls[0])
        self.assertIn("g.expected_active_generation_id", transaction.calls[1])
        self.assertIn("building_control_record_sha256", transaction.calls[1])

    async def test_publish_closes_then_cas_flips_pointer_and_preserves_previous(self) -> None:
        transaction = _PublishTransaction()

        closure, verifier = await self._publish(transaction)

        self.assertEqual(14, closure["product_stages"]["count"])
        self.assertEqual(1, closure["product_operations"]["count"])
        self.assertEqual(1, closure["product_records"]["count"])
        self.assertEqual("product-generation-closure/v1", closure["schema_version"])
        self.assertEqual(
            {"initial_sha256", "locked_pretransition_sha256", "post_state_sha256"},
            set(closure["control_records"]),
        )
        self.assertEqual(0, closure["call_occurrences"]["count"])
        self.assertEqual(0, closure["import_occurrences"]["count"])
        self.assertEqual(64, len(closure["product_plan_sha256"]))
        verifier.assert_awaited_once()
        cas_index = next(
            index for index, query in enumerate(transaction.calls)
            if "SET scope.publication_version" in query
        )
        publish_index = next(
            index for index, query in enumerate(transaction.calls)
            if "scope.active_generation_id = $generation_id" in query
        )
        post_state_index = next(
            index for index, query in enumerate(transaction.calls)
            if "current_quality_status" in query
        )
        self.assertLess(cas_index, publish_index)
        self.assertLess(publish_index, post_state_index)
        self.assertIn("previous.publication_status = 'superseded'", transaction.calls[publish_index])
        self.assertFalse(any("DETACH DELETE n" in query for query in transaction.calls))

    async def test_concurrent_publication_cas_conflict_fails_before_graph_readback(self) -> None:
        transaction = _PublishTransaction(cas_conflict=True)

        with self.assertRaisesRegex(GraphGenerationError, "publication CAS conflict"):
            await self._publish(transaction)

        self.assertEqual(1, len(transaction.calls))

    async def test_locked_control_hash_mismatch_fails_before_graph_readback(self) -> None:
        transaction = _PublishTransaction(tamper_locked_control_hash=True)

        with self.assertRaisesRegex(
            GraphGenerationError, "locked control pre-transition read-back hash",
        ):
            await self._publish(transaction)

        self.assertEqual(1, len(transaction.calls))

    async def test_product_verifier_failure_never_reaches_pointer_publish(self) -> None:
        transaction = _PublishTransaction()
        with self.assertRaisesRegex(GraphGenerationError, "product plan verification"):
            await self._publish(
                transaction,
                verification_error=ProductWriteContractError(
                    "final product mismatch"
                ),
            )
        self.assertFalse(any(
            "scope.active_generation_id = $generation_id" in query
            for query in transaction.calls
        ))

    async def test_incomplete_product_plan_fails_before_cas_lock(self) -> None:
        transaction = _PublishTransaction()
        complete_plan = _product_plan()
        incomplete_plan = combine_product_write_batches(
            complete_plan.batches[:-1],
            analysis_scope_id=SCOPE,
            generation_id="run:new",
        )

        with self.assertRaisesRegex(
            GraphGenerationError, "complete closed product stage set",
        ):
            await publish_generation(
                _Client(transaction),
                generation_id="run:new",
                quality_status="completed",
                call_resolution_ledger=[],
                product_plan=incomplete_plan,
            )

        self.assertEqual([], transaction.calls)

    async def test_unresolved_occurrence_upgrades_quality_and_closes_hash(self) -> None:
        ledger = [_call_row()]
        transaction = _PublishTransaction()

        closure, _verifier = await self._publish(
            transaction,
            call_resolution_ledger=ledger,
        )

        self.assertEqual("completed_with_findings", closure["quality_status"])
        self.assertEqual(1, closure["call_occurrences"]["count"])
        self.assertEqual({"unresolved": 1}, closure["call_occurrences"]["by_outcome"])
        self.assertEqual({"missing": 1}, closure["call_occurrences"]["by_reason"])

    async def test_unresolved_data_object_upgrades_quality_without_graph_entity(self) -> None:
        transaction = _PublishTransaction()

        closure, _verifier = await self._publish(
            transaction,
            unresolved_data_objects=2,
        )

        self.assertEqual("completed_with_findings", closure["quality_status"])
        self.assertEqual(1, closure["product_records"]["count"])

    async def test_duplicate_occurrence_ledger_fails_before_graph_readback(self) -> None:
        transaction = _PublishTransaction()
        plan = _product_plan(unresolved_calls=2)
        with self.assertRaisesRegex(GraphGenerationError, "call occurrence ledger"):
            await publish_generation(
                _Client(transaction),
                generation_id="run:new",
                quality_status="completed_with_findings",
                call_resolution_ledger=[_call_row(), _call_row()],
                product_plan=plan,
            )
        self.assertEqual([], transaction.calls)

    async def test_unresolved_import_occurrence_upgrades_quality_and_closes_hash(self) -> None:
        ledger = [_import_row()]
        transaction = _PublishTransaction()

        closure, _verifier = await self._publish(
            transaction,
            import_resolution_ledger=ledger,
        )

        self.assertEqual("completed_with_findings", closure["quality_status"])
        self.assertEqual(1, closure["import_occurrences"]["count"])
        self.assertEqual(
            {"unresolved": 1}, closure["import_occurrences"]["by_outcome"],
        )

    async def test_publish_rejects_more_than_one_active_generation(self) -> None:
        transaction = _PublishTransaction(active_count=2)

        with self.assertRaisesRegex(
            GraphGenerationError, "exactly one active generation",
        ):
            await self._publish(transaction)

    async def test_failure_terminal_status_and_staging_delete_share_transaction(self) -> None:
        transaction = _TerminateTransaction()

        await terminate_generation(
            _Client(transaction),
            generation_id="run:new",
            status="failed",
            reason="injected failure",
        )

        self.assertIn("SET g.generation_status = $status", transaction.calls[0])
        self.assertIn("DETACH DELETE n", transaction.calls[1])
        self.assertIn("WHERE NOT n:AnalysisGeneration", transaction.calls[1])
        self.assertNotIn("ANALYZER_OWNED", transaction.calls[1])


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
            "analysis_scope_id": SCOPE,
            "generation_id": "run:published",
            "status": "completed",
            "call_resolution_summary_json": json.dumps({
                "total": 0, "unresolved": 0,
                "by_outcome": {}, "by_reason": {}, "sha256": "0" * 64,
            }),
            "import_resolution_summary_json": json.dumps({
                "total": 0, "unresolved": 0,
                "by_outcome": {}, "by_reason": {}, "sha256": "0" * 64,
            }),
        }

    def test_scoped_session_injects_resolved_generation_into_every_read(self) -> None:
        driver = _SyncDriver(self._record())

        with completed_generation_session(driver, "neo4j") as session:
            session.run("MATCH (n {generation_id:$generation_id}) RETURN n")
            self.assertEqual("completed", session.quality_status)
            self.assertEqual(SCOPE, session.analysis_scope_id)
            self.assertEqual(0, session.call_resolution["total"])
            self.assertEqual(0, session.import_resolution["total"])

        self.assertEqual(
            "run:published",
            driver.session_value.calls[-1][1]["generation_id"],
        )
        self.assertEqual(
            SCOPE,
            driver.session_value.calls[-1][1]["analysis_scope_id"],
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

    def test_scoped_session_rejects_analysis_scope_override(self) -> None:
        driver = _SyncDriver(self._record())

        with completed_generation_session(driver, "neo4j") as session:
            with self.assertRaises(CompletedGenerationUnavailable):
                session.run("RETURN $analysis_scope_id", analysis_scope_id="other")

    def test_generation_without_call_resolution_closure_fails_closed(self) -> None:
        driver = _SyncDriver({
            "analysis_scope_id": SCOPE,
            "generation_id": "run:published",
            "status": "completed",
        })
        with self.assertRaisesRegex(CompletedGenerationUnavailable, "call resolution"):
            with completed_generation_session(driver, "neo4j"):
                pass

    def test_generation_without_import_resolution_closure_fails_closed(self) -> None:
        record = self._record()
        del record["import_resolution_summary_json"]
        driver = _SyncDriver(record)
        with self.assertRaisesRegex(CompletedGenerationUnavailable, "import resolution"):
            with completed_generation_session(driver, "neo4j"):
                pass


if __name__ == "__main__":
    unittest.main()
