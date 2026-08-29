"""Analysis registry, destructive initialization and run identity contracts."""
from __future__ import annotations

import unittest
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipeline.models import analysis_meta
from pipeline.models.analysis_context import ControlSubtreeNode, ReferencedSymbolContext
from pipeline.models.data_object_usage import (
    DataObjectUsageEvidence,
    DatabaseLinkComponentEvidence,
    QualifiedColumnUsage,
    UnqualifiedIdentifierUsage,
)
from pipeline.models.external_reference_resolution import ResolvedExternalReferenceLink
from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord
from pipeline.models.analysis_meta import AnalysisRun, Codebase
from pipeline.models.semantic_frame import SemanticFrame, SlotSpec, StructuralProfile
from pipeline.execution import run_executor, run_registry
from pipeline.execution.pause_controller import PauseController
from pipeline.execution.sealed_analysis_inputs import SealedAnalysisInputs
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
        codebase = Codebase(
            root=Path("source"), corpus_root=Path("."), strategy="framework"
        )
        fixed_time = datetime(2026, 7, 17, 1, 2, 3, tzinfo=timezone.utc)
        with patch.object(analysis_meta, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_time
            first = AnalysisRun.now(codebase, Path("output"))
            second = AnalysisRun.now(codebase, Path("output"))

        self.assertNotEqual(first.run_id, second.run_id)

    def test_analysis_scope_id_is_stable_across_runs_of_the_same_codebase(self) -> None:
        codebase = Codebase(
            root=Path("source"), corpus_root=Path("."), strategy="framework"
        )

        first = AnalysisRun.now(codebase, Path("output"))
        second = AnalysisRun.now(codebase, Path("output"))

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(first.analysis_scope_id, second.analysis_scope_id)
        self.assertEqual(codebase.analysis_scope_id, first.analysis_scope_id)
        self.assertRegex(
            first.analysis_scope_id,
            r"^analysis-scope/v2:[0-9a-f]{64}$",
        )

    def test_analysis_scope_id_separates_source_roots_and_strategies(self) -> None:
        framework = Codebase(
            root=Path("source-a"), corpus_root=Path("."), strategy="framework"
        )
        other_root = Codebase(
            root=Path("source-b"), corpus_root=Path("."), strategy="framework"
        )
        dbms = Codebase(
            root=Path("source-a"), corpus_root=Path("."), strategy="dbms"
        )

        self.assertNotEqual(framework.analysis_scope_id, other_root.analysis_scope_id)
        self.assertNotEqual(framework.analysis_scope_id, dbms.analysis_scope_id)

    def test_partial_source_population_has_a_distinct_stable_scope(self) -> None:
        complete = Codebase(
            root=Path("source"), corpus_root=Path("."), strategy="framework"
        )
        selected = Codebase(
            root=Path("source"),
            corpus_root=Path("."),
            strategy="framework",
            selected_source_ids=("b.c", "a.c"),
        )
        same_selected = Codebase(
            root=Path("source"),
            corpus_root=Path("."),
            strategy="framework",
            selected_source_ids=("a.c", "b.c"),
        )
        other_selected = Codebase(
            root=Path("source"),
            corpus_root=Path("."),
            strategy="framework",
            selected_source_ids=("a.c",),
        )

        self.assertNotEqual(complete.analysis_scope_id, selected.analysis_scope_id)
        self.assertEqual(selected.analysis_scope_id, same_selected.analysis_scope_id)
        self.assertNotEqual(selected.analysis_scope_id, other_selected.analysis_scope_id)

    def test_nested_analysis_context_types_are_checkpoint_allowlisted(self) -> None:
        self.assertIn(ReferencedSymbolContext, run_executor._STATE_MSGPACK_ALLOWLIST)
        self.assertIn(ControlSubtreeNode, run_executor._STATE_MSGPACK_ALLOWLIST)

    def test_product_state_nested_types_are_checkpoint_allowlisted(self) -> None:
        for runtime_type in (
            ExternalSourceFile,
            ExternalSourceRecord,
            ResolvedExternalReferenceLink,
            DataObjectUsageEvidence,
            QualifiedColumnUsage,
            UnqualifiedIdentifierUsage,
            DatabaseLinkComponentEvidence,
            StructuralProfile,
            SlotSpec,
            SemanticFrame,
        ):
            self.assertIn(runtime_type, run_executor._STATE_MSGPACK_ALLOWLIST)

    def test_call_resolution_workflow_update_is_captured_exactly_once(self) -> None:
        row = {
            "call_id": "call:1", "ordinal": 0,
            "caller_id": "code:sample.c:run",
            "resolution_status": "internal",
            "adapter_schema": "analyzer-c-semantics/v1",
            "outcome": "edge", "reason": "", "scope": "internal",
            "callee_id": "code:sample.c:run",
            "external_identity_key": "", "declaration_fact_id": "def:run",
            "candidate_fact_ids": ["def:run"],
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

    def test_every_strategy_requires_even_an_explicitly_empty_call_ledger(self) -> None:
        with self.assertRaisesRegex(ValueError, "did not produce"):
            run_executor._require_call_resolution_producer("framework", False)
        run_executor._require_call_resolution_producer("framework", True)
        with self.assertRaisesRegex(ValueError, "did not produce"):
            run_executor._require_call_resolution_producer("dbms", False)
        run_executor._require_call_resolution_producer("dbms", True)

    def test_import_resolution_workflow_update_is_captured_exactly_once(self) -> None:
        import_id = "a" * 64
        row = {
            "occurrence_id": f"{import_id}:0", "import_id": import_id,
            "ordinal": 0, "entry_ordinal": 0, "evidence_version": "1.1.0",
            "source_id": "sample.c", "line": 1,
            "end_line": 1, "range": {"char_offset": 0, "char_length": 20},
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
            "resolved_module_ids": [],
        }

        ledger, seen = run_executor._capture_import_resolution_update(
            [], False, {"import_resolution_ledger": [row]},
        )

        self.assertTrue(seen)
        self.assertEqual([row], ledger)
        with self.assertRaisesRegex(ValueError, "more than one workflow producer"):
            run_executor._capture_import_resolution_update(
                ledger, seen, {"import_resolution_ledger": [row]},
            )
        with self.assertRaisesRegex(ValueError, "did not produce"):
            run_executor._require_import_resolution_producer(False)
        run_executor._require_import_resolution_producer(True)


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


class FinalProductStateContractTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _complete_state() -> dict:
        state = {
            field: [] for field in run_executor._PRODUCT_STATE_FIELDS
        }
        state.update({
            "external_reference_ledger": {},
            "component_member_ids_by_component_id": {},
            "call_resolution_ledger": [],
            "import_resolution_ledger": [],
        })
        return state

    async def test_final_checkpoint_requires_every_product_producer_field(self) -> None:
        class App:
            async def aget_state(self, _config):
                return SimpleNamespace(values={"call_resolution_ledger": []})

        with self.assertRaisesRegex(ValueError, "missing product producer fields"):
            await run_executor._read_final_workflow_state(App(), {})

    async def test_final_checkpoint_returns_the_single_complete_state(self) -> None:
        expected = self._complete_state()

        class App:
            async def aget_state(self, _config):
                return SimpleNamespace(values=expected)

        self.assertEqual(
            expected,
            await run_executor._read_final_workflow_state(App(), {}),
        )

    def test_streamed_and_final_call_ledgers_must_be_identical(self) -> None:
        state = self._complete_state()
        row = {
            "call_id": "call:1",
            "ordinal": 0,
            "caller_id": "code:sample.c:run",
            "resolution_status": "internal",
            "adapter_schema": "analyzer-c-semantics/v1",
            "outcome": "edge",
            "reason": "",
            "scope": "internal",
            "callee_id": "code:sample.c:run",
            "external_identity_key": "",
            "declaration_fact_id": "def:run",
            "candidate_fact_ids": ["def:run"],
            "preprocessing_evidence_ids": [],
            "macro_fact_ids": [],
        }
        state["call_resolution_ledger"] = [row]

        with self.assertRaisesRegex(ValueError, "differs from its sole streamed"):
            run_executor._build_complete_product_input(
                final_state=state,
                run=SimpleNamespace(
                    analysis_scope_id="scope-1", run_id="generation-1",
                ),
                source_ast_closure=SimpleNamespace(),
                glossary=SimpleNamespace(),
                call_resolution_ledger=[],
                import_resolution_ledger=[],
                prepublish_wall_time_ms=1,
                prepublish_performance_artifact_hash="a" * 64,
            )

    def test_performance_artifact_is_canonical_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schema_version": "prepublish-performance/v1",
                "prepublish_wall_time_ms": 123,
            }

            first = run_executor._write_content_addressed_performance_artifact(
                root, payload,
            )
            second = run_executor._write_content_addressed_performance_artifact(
                root, payload,
            )

            self.assertEqual(first, second)
            artifacts = list((root / "performance").glob("*.json"))
            self.assertEqual([f"{first}.json"], [path.name for path in artifacts])
            self.assertEqual(
                first,
                hashlib.sha256(artifacts[0].read_bytes()).hexdigest(),
            )


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
                    codebase=Codebase(
                        root=Path("source"),
                        corpus_root=Path("."),
                        strategy="framework",
                    ),
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
                    codebase=Codebase(
                        root=Path("source"),
                        corpus_root=Path("."),
                        strategy="framework",
                    ),
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

    async def test_unsealed_model_runtime_precedes_neo4j_client(self) -> None:
        class Closure:
            artifacts: tuple = ()

            @staticmethod
            def as_dict() -> dict:
                return {"schema_version": "source-ast-closure/v1", "artifacts": []}

        strategy = SimpleNamespace(name="framework", prompts_dir="llm/prompts/framework")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(run_executor, "verify_source_ast_closure", return_value=Closure()),
                patch.object(
                    run_executor, "ast_and_source_roots", return_value=(root, root),
                ),
                patch.object(
                    run_executor,
                    "load_model_runtime_capabilities",
                    side_effect=ValueError("runtime capability is not sealed"),
                ),
                patch.object(run_executor, "Neo4jClient") as neo4j_client,
            ):
                with self.assertRaisesRegex(ValueError, "runtime capability"):
                    await run_executor.execute_run(
                        strategy=strategy,
                        codebase=Codebase(
                            root=root,
                            corpus_root=root,
                            strategy="framework",
                        ),
                        run_output_root=root / "runs",
                    )
            neo4j_client.assert_not_called()
            manifests = list((root / "runs").glob("*/manifest.json"))
            self.assertEqual(1, len(manifests))
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual("failed_preflight", manifest["status"])
            self.assertIsNone(manifest["sealed_analysis_inputs"])

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
                patch.object(run_executor, "load_model_runtime_capabilities", return_value=object()),
                patch.object(
                    run_executor,
                    "build_sealed_analysis_inputs",
                    return_value=SealedAnalysisInputs(
                        payload={
                            "analysis_scope_files": {"file_count": 0},
                            "ddl_files": {"supplied": False},
                        },
                        sha256="a" * 64,
                    ),
                ),
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

    async def test_success_writes_every_product_stage_before_single_publish(self) -> None:
        class Closure:
            artifacts: tuple = ()

            @staticmethod
            def as_dict() -> dict:
                return {"schema_version": "source-ast-closure/v1", "artifacts": []}

        final_state = FinalProductStateContractTest._complete_state()

        class SuccessfulApp:
            async def astream(self, *_args, **_kwargs):
                yield {
                    "resolution": {
                        "call_resolution_ledger": [],
                        "import_resolution_ledger": [],
                    }
                }

            async def aget_state(self, _config):
                return SimpleNamespace(values=final_state)

        batches = [
            SimpleNamespace(stage_name="code_structure"),
            SimpleNamespace(stage_name="analysis_health"),
        ]
        product_plan = SimpleNamespace(batches=batches)
        events: list[str] = []

        async def write_stage(_client, batch):
            events.append(f"write:{batch.stage_name}")
            return SimpleNamespace(
                stage_name=batch.stage_name,
                operation_count=1,
                idempotent_replay=False,
            )

        async def publish(_client, **kwargs):
            self.assertIs(product_plan, kwargs["product_plan"])
            events.append("publish")
            return {
                "schema_version": "product-generation-closure/v1",
                "analysis_scope_id": kwargs["product_plan"].scope_id
                if hasattr(kwargs["product_plan"], "scope_id") else "scope",
                "generation_id": kwargs["generation_id"],
                "quality_status": "completed",
                "product_plan_sha256": "a" * 64,
                "product_stages": {"count": 2, "sha256": "b" * 64},
                "product_operations": {"count": 2, "sha256": "c" * 64},
                "product_records": {"count": 2, "sha256": "d" * 64},
            }

        neo = SimpleNamespace(close=AsyncMock())
        strategy = SimpleNamespace(
            name="framework", prompts_dir="llm/prompts/framework",
        )

        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(run_executor, "verify_source_ast_closure", return_value=Closure()),
                patch.object(run_executor, "load_model_runtime_capabilities", return_value=object()),
                patch.object(
                    run_executor,
                    "build_sealed_analysis_inputs",
                    return_value=SealedAnalysisInputs(
                        payload={
                            "analysis_scope_files": {"file_count": 0},
                            "ddl_files": {"supplied": False},
                        },
                        sha256="a" * 64,
                    ),
                ),
                patch.object(
                    run_executor, "ast_and_source_roots",
                    return_value=(Path(temporary), Path(temporary)),
                ),
                patch.object(run_executor, "Neo4jClient", return_value=neo),
                patch.object(run_executor, "ensure_schema", new=AsyncMock()),
                patch.object(run_executor, "begin_generation", new=AsyncMock()),
                patch.object(run_executor, "terminate_generation", new=AsyncMock()),
                patch.object(
                    run_executor, "build_langgraph_workflow",
                    return_value=SuccessfulApp(),
                ),
                patch.object(
                    run_executor,
                    "build_complete_product_write_plan",
                    return_value=product_plan,
                ),
                patch.object(run_executor, "write_product_batch", side_effect=write_stage),
                patch.object(run_executor, "publish_generation", side_effect=publish),
                patch.object(run_executor.response_cache, "sweep"),
            ):
                await run_executor.execute_run(
                    strategy=strategy,
                    codebase=Codebase(
                        root=Path(temporary),
                        strategy="framework",
                        corpus_root=Path(temporary),
                    ),
                    run_output_root=Path(temporary) / "runs",
                )

        self.assertEqual(
            ["write:code_structure", "write:analysis_health", "publish"],
            events,
        )
        neo.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
