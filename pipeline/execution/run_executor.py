"""Run 1회 실행 어댑터 — run 경로 준비 + LangGraph workflow + 원자적 공개.

흐름:
1. 선택된 run-output 루트를 초기화한다. 미지정 시 제품 밖 playground를 사용한다.
2. ``Run`` 메타 생성 + Neo4j 인덱스 / 제약 보장.
3. ``CostBudget`` + ``MemorySaver`` (Checkpointer) + ``PauseController`` 생성.
4. Strategy 의 workflow 빌더 호출 → ``astream(stream_mode="updates")``.
5. ``usage_summary.json`` 집계.

**run 정책** — 매 ``/analyze`` 호출 = 독립 run (``run_id`` 가 유일 식별자). 진행은 NDJSON stream.

**일시정지 / 재개 / 중단** (소프트 — 프로세스가 살아있는 동안의 hold):
- ``PauseController`` 를 만들어 ``run_registry`` 에 등록 → control 엔드포인트가 신호.
- astream 소비 루프가 step 경계마다 ``pause.gate()`` 로 소비(paused 면 hold).
- stop 은 ``RunStopped`` 로 정상 종료(실패 아님). 서버를 끄면 run 도 끝난다(디스크 재개 X).
- Checkpointer(MemorySaver)는 run 1회 안의 state 전파용 — wipe / thread_id 정책 그대로.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from graph.schema import graph_edges, graph_nodes
from pipeline.models import analysis_context, human_glossary
from pipeline.models.data_object_usage import (
    DataObjectUsageEvidence,
    DatabaseLinkComponentEvidence,
    QualifiedColumnUsage,
    UnqualifiedIdentifierUsage,
)
from pipeline.models.external_reference_resolution import ResolvedExternalReferenceLink
from pipeline.models.external_source import ExternalSourceFile, ExternalSourceRecord
from pipeline.models.semantic_frame import SemanticFrame, SlotSpec, StructuralProfile
from pipeline.models.analysis_meta import AnalysisRun
from pipeline.models.analysis_meta import Codebase
from pipeline.models.human_glossary import HumanGlossary, load_human_glossary
from shared.config.settings import SETTINGS

from llm import response_cache
from llm.embedder import validate_runtime_configuration as validate_embedding_runtime
from llm.model_runtime_capabilities import load_model_runtime_capabilities
from llm.usage.cost_budget import CostBudget
from shared.events.runtime_logger import (
    attach_run_file_handlers,
    detach_run_file_handlers,
    log_error,
    log_info,
    redact_sensitive_text,
)
from shared.events.ndjson_event_stream import NDJSONStream
from graph.neo4j_client import Neo4jClient
from graph.product_batch_writer import write_product_batch
from graph.schema.constraints import ensure_schema
from graph.generation import begin_generation, publish_generation, terminate_generation
from pipeline.models.call_resolution import normalize_call_resolution_ledger, summarize_call_resolution_ledger
from pipeline.models.import_resolution import (
    normalize_import_resolution_ledger,
    summarize_import_resolution_ledger,
)
from graph.connection_context import get_override
from shared.files.cache import SourceFileCache
from shared.files.run_cache_context import reset_run_file_cache, set_run_file_cache
from pipeline.execution.analysis_handoff import AnalysisHandoff, analysis_llm_slots
from pipeline.execution.langgraph_builder import build_langgraph_workflow
from pipeline.execution.pause_controller import PauseController
from pipeline.execution.sealed_analysis_inputs import (
    SealedAnalysisInputs,
    build_sealed_analysis_inputs,
    write_sealed_analysis_inputs,
)
from pipeline.publication.product_plan import (
    CompleteProductPlanInput,
    build_complete_product_write_plan,
)
from pipeline.execution.run_registry import clear as clear_active_run
from pipeline.execution.run_registry import set_active as set_active_run
from pipeline.execution.run_registry import ConcurrentAnalysisRunError
from pipeline.execution.run_output_context import (
    reset_response_locale,
    reset_run_dir,
    set_response_locale,
    set_run_dir,
)
from pipeline.steps.step2_load_ast.dispatch import ast_and_source_roots
from pipeline.analysis_ir.parser_facts.source_ast_closure import (
    SourceAstClosure,
    verify_source_ast_closure,
)
from pipeline.steps.step3_load_external.inventory import inventory_project


_RUN_OUTPUT_OWNER_MARKER = ".robo-data-analyzer-output"
_RUN_OUTPUT_OWNER_MARKER_CONTENT = "robo-data-analyzer\n"

_PRODUCT_STATE_FIELDS = (
    "packages",
    "modules",
    "code_nodes",
    "belongs_to_edges",
    "has_member_edges",
    "parent_of_edges",
    "statement_next_edges",
    "tables",
    "columns",
    "has_column_edges",
    "fk_edges",
    "external_source_records",
    "resolved_external_reference_links",
    "external_reference_ledger",
    "calls_edges",
    "call_resolution_ledger",
    "reads_writes_edges",
    "rules",
    "examples",
    "has_rule_edges",
    "has_example_edges",
    "component_member_ids_by_component_id",
    "search_segments",
)


async def _read_final_workflow_state(app: Any, config: dict) -> dict[str, Any]:
    """Read the one final checkpoint after the workflow has completed."""
    snapshot = await app.aget_state(config)
    values = getattr(snapshot, "values", None)
    if not isinstance(values, Mapping):
        raise ValueError("completed workflow checkpoint has no mapping state")
    missing = sorted(field for field in _PRODUCT_STATE_FIELDS if field not in values)
    if missing:
        raise ValueError(
            "completed workflow state is missing product producer fields: "
            + ", ".join(missing)
        )
    return dict(values)


def _build_complete_product_input(
    *,
    final_state: Mapping[str, Any],
    run: AnalysisRun,
    source_ast_closure: SourceAstClosure,
    glossary: HumanGlossary,
    call_resolution_ledger: Sequence[Mapping[str, Any]],
    import_resolution_ledger: Sequence[Mapping[str, Any]],
    prepublish_wall_time_ms: int,
    prepublish_performance_artifact_hash: str,
) -> CompleteProductPlanInput:
    """Bind the final workflow state to the closed product-plan contract."""
    final_call_ledger = normalize_call_resolution_ledger(
        final_state["call_resolution_ledger"]
    )
    captured_call_ledger = normalize_call_resolution_ledger(
        call_resolution_ledger
    )
    if final_call_ledger != captured_call_ledger:
        raise ValueError(
            "final workflow call ledger differs from its sole streamed producer"
        )
    final_import_ledger = normalize_import_resolution_ledger(
        final_state["import_resolution_ledger"]
    )
    captured_import_ledger = normalize_import_resolution_ledger(
        import_resolution_ledger
    )
    if final_import_ledger != captured_import_ledger:
        raise ValueError(
            "final workflow import ledger differs from its sole streamed producer"
        )

    return CompleteProductPlanInput(
        analysis_scope_id=run.analysis_scope_id,
        generation_id=run.run_id,
        source_ast_closure=source_ast_closure,
        human_glossary=glossary,
        packages=final_state["packages"],
        modules=final_state["modules"],
        code_nodes=final_state["code_nodes"],
        belongs_to_edges=final_state["belongs_to_edges"],
        has_member_edges=final_state["has_member_edges"],
        parent_of_edges=final_state["parent_of_edges"],
        statement_next_edges=final_state["statement_next_edges"],
        tables=final_state["tables"],
        columns=final_state["columns"],
        has_column_edges=final_state["has_column_edges"],
        foreign_key_edges=final_state["fk_edges"],
        external_source_records=final_state["external_source_records"],
        resolved_external_reference_links=(
            final_state["resolved_external_reference_links"]
        ),
        external_reference_ledger=final_state["external_reference_ledger"],
        calls_edges=final_state["calls_edges"],
        call_resolution_ledger=final_call_ledger,
        reads_writes_edges=final_state["reads_writes_edges"],
        rules=final_state["rules"],
        examples=final_state["examples"],
        has_rule_edges=final_state["has_rule_edges"],
        has_example_edges=final_state["has_example_edges"],
        component_member_ids_by_component_id=(
            final_state["component_member_ids_by_component_id"]
        ),
        search_segments=final_state["search_segments"],
        prepublish_wall_time_ms=prepublish_wall_time_ms,
        prepublish_performance_artifact_hash=(
            prepublish_performance_artifact_hash
        ),
    )


def _write_content_addressed_performance_artifact(
    run_dir: Path,
    payload: Mapping[str, Any],
) -> str:
    """Persist one canonical performance ledger and return its byte hash."""
    encoded = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    artifact_dir = run_dir / "performance"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_dir / f"{digest}.json"
    if target.exists():
        if target.read_bytes() != encoded:
            raise ValueError(
                "content-addressed performance artifact hash collision"
            )
    else:
        temporary = artifact_dir / f"{digest}.json.tmp"
        temporary.write_bytes(encoded)
        temporary.replace(target)
    return digest


def _capture_call_resolution_update(
    current: list[dict],
    seen: bool,
    update: Any,
) -> tuple[list[dict], bool]:
    """Capture the workflow's sole complete call ledger without merging partial state."""
    if not isinstance(update, dict) or "call_resolution_ledger" not in update:
        return current, seen
    if seen:
        raise ValueError("call_resolution_ledger has more than one workflow producer")
    return normalize_call_resolution_ledger(update["call_resolution_ledger"]), True


def _require_call_resolution_producer(strategy_name: str, seen: bool) -> None:
    if not seen:
        raise ValueError(
            f"{strategy_name} workflow did not produce a complete "
            "call_resolution_ledger"
        )


def _capture_import_resolution_update(
    current: list[dict],
    seen: bool,
    update: Any,
) -> tuple[list[dict], bool]:
    """Capture step2's sole complete import-entry ledger."""
    if not isinstance(update, dict) or "import_resolution_ledger" not in update:
        return current, seen
    if seen:
        raise ValueError("import_resolution_ledger has more than one workflow producer")
    return normalize_import_resolution_ledger(
        update["import_resolution_ledger"]
    ), True


def _require_import_resolution_producer(seen: bool) -> None:
    if not seen:
        raise ValueError(
            "workflow did not produce a complete import_resolution_ledger"
        )


def _state_msgpack_allowlist() -> tuple[type, ...]:
    """체크포인트(MemorySaver·msgpack)에 직렬화되는 state dataclass 전부 — langgraph
    역직렬화 명시 allowlist.

    ``graph_nodes`` / ``graph_edges`` 에서 dataclass 를 자동 수집(새 노드/엣지 추가 시 자동
    등록 — 누락 0) + 컨텍스트 / 코드베이스 / run. ``allowed_msgpack_modules=True`` 는
    "전체 허용 + 미등록 경고"라 근본 해결이 아님 — 명시 목록이라야 경고 0 + 장래 STRICT 차단에도 안전.
    """
    types = [
        obj
        for mod in (graph_nodes, graph_edges, human_glossary)
        for obj in vars(mod).values()
        if isinstance(obj, type) and dataclasses.is_dataclass(obj)
    ]
    types += [
        obj
        for obj in vars(analysis_context).values()
        if isinstance(obj, type) and dataclasses.is_dataclass(obj)
    ]
    types += [
        Codebase,
        AnalysisRun,
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
    ]
    return tuple(types)


# import 시 1회 — 직렬화기 allowlist (run 마다 재계산 불필요).
_STATE_MSGPACK_ALLOWLIST = _state_msgpack_allowlist()


async def execute_run(
    strategy: Any,
    codebase: Codebase,
    stream: Optional[NDJSONStream] = None,
    run_output_root: Path | None = None,
) -> AnalysisRun:
    """AnalysisRun 1회 실행 — 매 호출이 독립 run (``run_id`` 가 유일 식별자).

    Args:
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``analysis_pipelines``).
        codebase: 분석 대상.
        stream: 프론트 NDJSON 스트림.
    """
    run_output_root = run_output_root or SETTINGS.run_output.dir
    run = AnalysisRun.now(codebase=codebase, run_output_root=run_output_root)
    pause = PauseController(stream)

    llm_semaphore = None
    try:
        # 단일 활성 슬롯은 output reset/Neo4j wipe보다 먼저 선점한다. 두 번째 run이
        # 파괴 작업에 진입한 뒤 control 슬롯만 덮어쓰는 경쟁을 막는다.
        set_active_run(run.run_id, pause)
    except ConcurrentAnalysisRunError as error:
        if stream is not None:
            stream.emit({
                "type": "error",
                "component": "run.registry",
                "operation": "claim_active_run",
                "message": str(error),
            })
            await stream.close()
        raise
    except BaseException:
        if stream is not None:
            await stream.close()
        raise

    output_context_token = None
    locale_context_token = None
    file_cache_context_token = None
    source_cache = SourceFileCache()
    try:
        file_cache_context_token = set_run_file_cache(source_cache)
        corpus_root = codebase.corpus_root.resolve()
        human_glossary = load_human_glossary(corpus_root)
        _reset_run_output_root(run_output_root, run.run_dir, strategy)
        output_context_token = set_run_dir(run.run_dir)
        locale_context_token = set_response_locale(codebase.locale)
        # ── 파일 핸들러 attach (ROBO_RUNTIME_EVENT_FILE_ENABLED=true 일 때만) ──
        # analyzer.log (INFO+) + error.log (WARNING+) → tail -f 로 실시간 추적 가능.
        # 운영기 OFF → 빈 리스트 → detach no-op → 디스크 I/O 0.
        # mkdir 직후 attach — 즉시 발생하는 ensure_schema / LangGraph 빌드 / step 로그 다 포함.
        file_handlers = attach_run_file_handlers(run.run_dir)
    except BaseException:
        if file_cache_context_token is not None:
            reset_run_file_cache(file_cache_context_token)
        if locale_context_token is not None:
            reset_response_locale(locale_context_token)
        if output_context_token is not None:
            reset_run_dir(output_context_token)
        clear_active_run(run.run_id)
        if stream is not None:
            await stream.close()
        raise

    run_started_at = time.monotonic()
    try:
        log_info(
            "run",
            "start",
            root=str(codebase.root),
            strategy=getattr(strategy, "name", "?"),
            run_id=run.run_id,
            analysis_scope_id=run.analysis_scope_id,
        )
        # Canonical source/AST/diagnostic population must close before any graph
        # mutation or generation publication begins.
        ast_root, parser_source_root = ast_and_source_roots(
            codebase.root, codebase.corpus_root,
        )
        source_ast_closure = verify_source_ast_closure(
            project_root=codebase.root,
            ast_root=ast_root,
            source_root=parser_source_root,
            source_cache=source_cache,
            selected_source_ids=codebase.selected_source_ids,
        )
        (
            source_ast_closure_ref,
            source_ast_closure_bytes,
            source_ast_closure_sha256,
        ) = _write_source_ast_closure(
            run.run_dir, source_ast_closure,
        )
        log_info(
            "run",
            "source_ast_closure_verified",
            sources=len(source_ast_closure.artifacts),
            ast_artifacts=len(source_ast_closure.artifacts),
            diagnostics=len(source_ast_closure.artifacts),
        )
        # The parsed object is process-cached for repeated consumers inside one
        # run.  Clear it at the run boundary so this preflight always verifies
        # the current sealed bytes instead of inheriting a previous run object.
        load_model_runtime_capabilities.cache_clear()
        model_runtime = load_model_runtime_capabilities(
            SETTINGS.analysis_llm.model_runtime_capabilities_ref,
            SETTINGS.analysis_llm.model_runtime_capabilities_sha256,
        )
        # Embedding is a required downstream paid/runtime boundary.  Reject an
        # impossible provider/model/endpoint tuple before spending generation
        # tokens; otherwise a run can finish all meanings and die at Step 8.
        validate_embedding_runtime()
        project_input_inventory = inventory_project(
            codebase.root,
            source_cache,
            selected_source_paths=(
                tuple(artifact.source_path for artifact in source_ast_closure.artifacts)
                if codebase.selected_source_ids else None
            ),
            excluded_roots=(run_output_root,),
        )
        sealed_analysis_inputs = build_sealed_analysis_inputs(
            codebase=codebase,
            neo4j_database=_resolved_neo4j_database(),
            project_input_paths=(item.path for item in project_input_inventory),
            source_ast_closure_ref=source_ast_closure_ref,
            source_ast_closure_bytes=source_ast_closure_bytes,
            source_ast_closure_sha256=source_ast_closure_sha256,
            strategy_prompts_dir=Path(
                getattr(strategy, "prompts_dir", "pipeline/semantic_tasks")
            ),
            human_glossary_manifest=human_glossary.manifest_dict(corpus_root),
            source_cache=source_cache,
            model_runtime=model_runtime,
            settings=SETTINGS,
            repository_root=Path(__file__).resolve().parents[2],
        )
        write_sealed_analysis_inputs(
            run.run_dir / "sealed-analysis-inputs.json", sealed_analysis_inputs,
        )
        _write_initial_manifest(run, sealed_analysis_inputs)
        log_info(
            "run",
            "analysis_inputs_sealed",
            sealed_analysis_inputs_sha256=sealed_analysis_inputs.sha256,
            analysis_scope_files=(
                sealed_analysis_inputs.payload["analysis_scope_files"]["file_count"]
            ),
            ddl_files=(
                sealed_analysis_inputs.payload["ddl_files"].get("file_count", 0)
            ),
        )
        neo_client = Neo4jClient(
            generation_id=run.run_id,
            analysis_scope_id=run.analysis_scope_id,
        )
        generation_started = False
        generation_terminal = False
        try:
            await ensure_schema(neo_client)
            await begin_generation(
                neo_client,
                generation_id=run.run_id,
                strategy=getattr(strategy, "name", "?"),
                source_ast_closure=source_ast_closure.as_dict(),
            )
            generation_started = True
            _update_run_manifest(run.run_dir, status="building")
            log_info(
                "run", "neo4j_ready",
                database=_resolved_neo4j_database(),
                generation_id=run.run_id,
                wiped=False,
            )

            # 응답 캐시 정리 — 프롬프트를 고칠 때마다 다시는 안 읽히는 항목이 쌓인다.
            # run 시작 1회, 상한 초과 시에만 실제로 지운다.
            response_cache.sweep()

            cost_budget = CostBudget()
            # 체크포인트(msgpack)가 직렬화하는 state dataclass(CodeNode/Table/Rule/Calls 등)를
            # 명시 allowlist 로 등록 — langgraph 가 "미등록 타입" 경고 없이 조용히 역직렬화하고,
            # 장래 STRICT 차단에도 안전. (=True 는 "전체허용+경고"라 근본 해결 아님.)
            checkpointer = MemorySaver(
                serde=JsonPlusSerializer(allowed_msgpack_modules=_STATE_MSGPACK_ALLOWLIST)
            )
            app = build_langgraph_workflow(strategy, checkpointer=checkpointer)

            # run 전체가 공유하는 **단 하나의** LLM 슬롯 (spec 096). step 마다 따로 만들면
            # 동시 실행 시 N 배가 한 서버로 몰려 과부하 — 실측으로 닫힌 길이다.
            # Step 7과 후처리가 같은 superstep에서 겹친다. 총량은 고정하고
            # 의존성 Summary/Rule/TABLE/후처리를 공정 차선으로 스케줄한다.
            llm_semaphore = analysis_llm_slots()
            # step7 → 후처리 인계 채널 — 분할이 확정된 시점을 후처리에 알려 배리어를 없앤다.
            analysis_handoff = AnalysisHandoff(llm_semaphore)

            # 일시정지/재개/중단 컨트롤러 — control 엔드포인트가 registry 로 찾아 신호.
            initial_state: dict = {"run": run}
            config = {
                "configurable": {
                    # LangGraph Checkpointer 가 요구하는 키 — run_id 를 그대로 쓴다.
                    "thread_id": run.run_id,
                    "strategy": strategy,
                    "codebase": codebase,
                    "run": run,
                    "cost_budget": cost_budget,
                    "stream": stream,
                    "pause": pause,
                    "llm_semaphore": llm_semaphore,   # run 공유 동시성 한도 (총량 단일 진실)
                    "analysis_handoff": analysis_handoff,  # step7 분할 확정 → 후처리 인계
                    "human_glossary": human_glossary,
                    "source_ast_closure": source_ast_closure,
                    "project_input_inventory": project_input_inventory,
                    "sealed_analysis_inputs": sealed_analysis_inputs,
                    # ★ run 스코프 소스 캐시 (spec 046 FR-008) — 파일당 디스크 read 1회.
                    #   종전엔 step 마다 새 인스턴스를 만들어 캐시가 무력화됐고, 같은 파일을
                    #   한 run 에서 5~8회 읽었다 (step2 안에서만 2회).
                    #   run 스코프라 "장수명 서버의 stale" 위험(SourceFileCache docstring)도 없다.
                    "source_cache": source_cache,
                }
            }

            if stream is not None:
                # emit 안에서 logger mirror 가 자동으로 analyzer.log 에 phase_event 한 줄.
                stream.emit({"type": "phase_event", "event": "start", "run_id": run.run_id})

            # step 경계마다 pause.gate() — paused 면 다음 step 앞에서 hold, stopped 면
            # RunStopped. stop 은 사용자 의도된 종료라 실패가 아님 → 정상 경로로 마무리.
            stopped = False
            call_resolution_ledger: list[dict] = []
            call_resolution_seen = False
            import_resolution_ledger: list[dict] = []
            import_resolution_seen = False
            workflow_started_at = time.monotonic()
            workflow_node_completion_timeline: list[dict[str, Any]] = []
            try:
                async for chunk in app.astream(initial_state, config=config, stream_mode="updates"):
                    if isinstance(chunk, dict):
                        for node_name, update in chunk.items():
                            call_resolution_ledger, call_resolution_seen = (
                                _capture_call_resolution_update(
                                    call_resolution_ledger,
                                    call_resolution_seen,
                                    update,
                                )
                            )
                            import_resolution_ledger, import_resolution_seen = (
                                _capture_import_resolution_update(
                                    import_resolution_ledger,
                                    import_resolution_seen,
                                    update,
                                )
                            )
                            workflow_node_completion_timeline.append({
                                "ordinal": len(workflow_node_completion_timeline),
                                "node": str(node_name),
                                "completed_elapsed_ms": int(
                                    (time.monotonic() - workflow_started_at) * 1000
                                ),
                            })
                            if stream is not None:
                                stream.emit({"type": "phase_event", "event": "node_done", "node": node_name})
                    await pause.gate()
            except Exception:
                # stopped 플래그가 켜진 채 빠져나온 예외 = 사용자 중단(RunStopped / 취소된
                # in-flight 의 래핑). 그 외 예외는 진짜 실패 → 그대로 위로 전파.
                if not pause.status()["isStopped"]:
                    # 실패해도 사용량 집계는 남긴다 (state artifact 덤프는 사용자 지시로 제거
                    # — 선택된 logs 루트에는 로그·jsonl·llm-prompts만 둔다).
                    try:
                        _summarize_usage(run.run_dir)
                        _summarize_analysis_findings(
                            run.run_dir,
                            call_resolution_ledger=call_resolution_ledger,
                            import_resolution_ledger=import_resolution_ledger,
                        )
                        log_info("run", "failure_usage_summarized", run_dir=str(run.run_dir))
                    except Exception as artifact_error:
                        log_error(
                            "run", "dump_failure_artifacts_failed", artifact_error,
                            stream_emit=stream.emit if stream is not None else None,
                            run_id=run.run_id,
                        )
                    raise
                stopped = True
                if stream is not None:
                    stream.emit({"type": "phase_event", "event": "stopped", "run_id": run.run_id})
                log_info("run", "stopped", run_id=run.run_id)

            if not stopped:
                _require_call_resolution_producer(strategy.name, call_resolution_seen)
                _require_import_resolution_producer(import_resolution_seen)
                _summarize_usage(run.run_dir)
                quality_summary = _summarize_analysis_findings(
                    run.run_dir,
                    call_resolution_ledger=call_resolution_ledger,
                    import_resolution_ledger=import_resolution_ledger,
                )
                final_state = await _read_final_workflow_state(app, config)
                prepublish_wall_time_ms = int(
                    (time.monotonic() - run_started_at) * 1000
                )
                prepublish_performance_artifact_hash = (
                    _write_content_addressed_performance_artifact(
                        run.run_dir,
                        {
                            "schema_version": "prepublish-performance/v1",
                            "analysis_scope_id": run.analysis_scope_id,
                            "generation_id": run.run_id,
                            "measurement_boundary": (
                                "run_start_through_final_workflow_state_before_"
                                "product_plan_build_and_product_writes"
                            ),
                            "prepublish_wall_time_ms": prepublish_wall_time_ms,
                            "workflow_node_completion_timeline": (
                                workflow_node_completion_timeline
                            ),
                        },
                    )
                )
                product_plan = build_complete_product_write_plan(
                    _build_complete_product_input(
                        final_state=final_state,
                        run=run,
                        source_ast_closure=source_ast_closure,
                        glossary=human_glossary,
                        call_resolution_ledger=call_resolution_ledger,
                        import_resolution_ledger=import_resolution_ledger,
                        prepublish_wall_time_ms=prepublish_wall_time_ms,
                        prepublish_performance_artifact_hash=(
                            prepublish_performance_artifact_hash
                        ),
                    )
                )
                product_stage_write_timings: list[dict[str, Any]] = []
                for batch in product_plan.batches:
                    stage_started_at = time.monotonic()
                    receipt = await write_product_batch(neo_client, batch)
                    product_stage_write_timings.append({
                        "stage_name": receipt.stage_name,
                        "wall_time_ms": int(
                            (time.monotonic() - stage_started_at) * 1000
                        ),
                        "operation_count": receipt.operation_count,
                        "idempotent_replay": receipt.idempotent_replay,
                    })
                publish_started_at = time.monotonic()
                graph_closure = await publish_generation(
                    neo_client,
                    generation_id=run.run_id,
                    quality_status=quality_summary["run_quality_status"],
                    call_resolution_ledger=call_resolution_ledger,
                    import_resolution_ledger=import_resolution_ledger,
                    product_plan=product_plan,
                )
                publish_transaction_wall_time_ms = int(
                    (time.monotonic() - publish_started_at) * 1000
                )
                quality_summary["run_quality_status"] = graph_closure["quality_status"]
                generation_terminal = True
                try:
                    _write_product_generation_closure(run.run_dir, graph_closure)
                    _write_content_addressed_performance_artifact(
                        run.run_dir,
                        {
                            "schema_version": "postcommit-performance/v1",
                            "analysis_scope_id": run.analysis_scope_id,
                            "generation_id": run.run_id,
                            "prepublish_performance_artifact_hash": (
                                prepublish_performance_artifact_hash
                            ),
                            "product_plan_sha256": graph_closure[
                                "product_plan_sha256"
                            ],
                            "product_stage_write_timings": (
                                product_stage_write_timings
                            ),
                            "publish_transaction_wall_time_ms": (
                                publish_transaction_wall_time_ms
                            ),
                            "total_wall_time_ms": int(
                                (time.monotonic() - run_started_at) * 1000
                            ),
                        },
                    )
                    _update_run_manifest(
                        run.run_dir,
                        status=quality_summary["run_quality_status"],
                        graph_closure=graph_closure,
                    )
                except Exception as artifact_error:
                    log_error(
                        "run", "write_published_generation_artifact_failed", artifact_error,
                        stream_emit=stream.emit if stream is not None else None,
                        run_id=run.run_id,
                    )

                if stream is not None:
                    stream.emit({
                        "type": "phase_event",
                        "event": "complete",
                        "run_id": run.run_id,
                        "quality_status": quality_summary["run_quality_status"],
                        "analysis_findings": quality_summary["total"],
                    })

                log_info(
                    "run",
                    "complete",
                    run_id=run.run_id,
                    elapsed_sec=f"{time.monotonic() - run_started_at:.1f}",
                    quality_status=quality_summary["run_quality_status"],
                    analysis_findings=quality_summary["total"],
                )

            if stopped:
                await terminate_generation(
                    neo_client,
                    generation_id=run.run_id,
                    status="stopped",
                    reason="user stop",
                )
                generation_terminal = True
                _update_run_manifest(run.run_dir, status="stopped")

        except BaseException as run_error:
            if generation_started and not generation_terminal:
                try:
                    await terminate_generation(
                        neo_client,
                        generation_id=run.run_id,
                        status="failed",
                        reason=redact_sensitive_text(
                            f"{type(run_error).__name__}: {run_error}"
                        ),
                    )
                    generation_terminal = True
                    _update_run_manifest(run.run_dir, status="failed")
                except Exception as terminal_error:
                    log_error(
                        "run", "terminate_failed_generation_failed", terminal_error,
                        stream_emit=stream.emit if stream is not None else None,
                        run_id=run.run_id,
                    )
            raise
        finally:
            await neo_client.close()
    except Exception as e:
        # 파이프라인 실패 — 스트림 consumer 가 알도록 에러 emit (조용한 실패 차단).
        try:
            manifest_path = run.run_dir / "manifest.json"
            if manifest_path.is_file():
                _update_run_manifest(run.run_dir, status="failed")
            else:
                _write_preflight_failure_manifest(run, e)
        except Exception as manifest_error:
            log_error(
                "run", "write_failed_run_manifest_failed", manifest_error,
                stream_emit=stream.emit if stream is not None else None,
                run_id=run.run_id,
            )
        log_error(
            "run", "run_failed", e,
            stream_emit=stream.emit if stream is not None else None,
            run_id=run.run_id,
            fatal=True,
        )
        raise
    finally:
        clear_active_run(run.run_id)
        if llm_semaphore is not None:
            log_info(
                "run", "llm_concurrency",
                capacity=llm_semaphore.capacity,
                peak_in_use=llm_semaphore.peak_in_use,
                in_use_at_close=llm_semaphore.in_use,
                waiting_at_close=llm_semaphore.waiting,
            )
        # neo close 다음 — Windows fd 충돌 회피 위해 attach 의 역순.
        detach_run_file_handlers(file_handlers)
        if output_context_token is not None:
            reset_run_dir(output_context_token)
        if locale_context_token is not None:
            reset_response_locale(locale_context_token)
        if file_cache_context_token is not None:
            reset_run_file_cache(file_cache_context_token)
        # 성공·실패 무관 스트림 종료 — 안 닫으면 consumer 가 stream_end 를 영구 대기 (hang).
        if stream is not None:
            await stream.close()
    return run


def _run_retention() -> int:
    """보존할 run 폴더 수 (새 run 포함). 직전 run 이 남아야 백업 없이 전후 비교가 된다."""
    raw = os.environ.get("ROBO_RUN_RETENTION", "2").strip()
    try:
        retention = int(raw)
    except ValueError as exc:
        raise ValueError(f"ROBO_RUN_RETENTION must be an integer >= 1; got {raw!r}") from exc
    if retention < 1:
        raise ValueError(f"ROBO_RUN_RETENTION must be >= 1; got {retention}")
    return retention


_RUN_DIR_NAME = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{12}$")
# manifest 를 읽을 수 없는 run(중단·구버전)의 보존 그룹. 조용히 버리지 않고 자기 그룹에서
# 같은 규칙으로 보존된다.
_UNKNOWN_RUN_STRATEGY = "unknown"


def _run_strategy(run_dir: Path) -> str:
    """run 폴더의 전략 이름 — 보존을 전략별로 세기 위한 분류 키."""
    manifest = run_dir / "manifest.json"
    try:
        return str(
            json.loads(manifest.read_text(encoding="utf-8")).get("strategy")
            or _UNKNOWN_RUN_STRATEGY
        )
    except (OSError, ValueError):
        return _UNKNOWN_RUN_STRATEGY


def _has_unrecovered_provider_evidence(run_dir: Path) -> bool:
    """Return whether a failed run owns a real, non-cache model response.

    Its prompt records are the lossless recovery source after a paid or local
    provider call. Preflight failures and cache-only replays use normal
    retention.
    """
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        usage = json.loads(
            (run_dir / "usage_summary.json").read_text(encoding="utf-8")
        ).get("total", {})
        calls = int(usage.get("calls", 0))
        cache_hits = int(usage.get("response_cache_hits", 0))
        generated_tokens = int(usage.get("input_tokens", 0)) + int(
            usage.get("output_tokens", 0)
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        manifest.get("status") == "failed"
        and calls > cache_hits
        and generated_tokens > 0
    )


def _reset_run_output_root(
    run_output_root: Path,
    run_dir: Path | None = None,
    strategy: object | None = None,
) -> None:
    """Analyzer가 소유한 실행 결과 루트 아래에 새 run 폴더를 준비한다.

    루트는 컨테이너다 — 통째로 지우지 않는다. run 은 ``root/<run_id>/`` 자기 폴더에만
    쓰며, 보존 개수(``ROBO_RUN_RETENTION``, 기본 2 = 새 run + 직전 run)를 넘는 가장
    오래된 run 폴더만 정리한다. 직전 run 의 record 가 남아야 전후 비교가 백업 없이
    성립한다(2026-08-07 구조 개편).
    """
    if not isinstance(run_output_root, Path):
        raise ValueError(f"run_output_root must be a Path; got {run_output_root!r}")
    if run_output_root.is_symlink():
        raise ValueError(f"실행 결과 루트는 심볼릭 링크일 수 없습니다: {run_output_root}")

    root = run_output_root.resolve()
    if root == Path.cwd().resolve():
        raise ValueError(f"실행 결과 루트는 현재 작업 디렉터리일 수 없습니다: {root}")
    if root == Path(root.anchor):
        raise ValueError(f"실행 결과 루트는 파일시스템 루트일 수 없습니다: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"실행 결과 루트가 디렉터리가 아닙니다: {root}")

    marker = root / _RUN_OUTPUT_OWNER_MARKER
    if root.exists() and any(root.iterdir()):
        if not marker.is_file():
            raise ValueError(f"Analyzer가 소유하지 않은 비어 있지 않은 디렉터리입니다: {root}")
        try:
            marker_content = marker.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"실행 결과 소유권 표식을 읽을 수 없습니다: {marker}") from exc
        if marker_content != _RUN_OUTPUT_OWNER_MARKER_CONTENT:
            raise ValueError(f"실행 결과 소유권 표식이 올바르지 않습니다: {marker}")
        # 구조 개편 전의 평면 산출물(루트 직속 파일·run 형식이 아닌 폴더)은 소유
        # 표식이 증명하는 우리 산출물이므로 제거한다 — 옛 로그를 새 run 결과로
        # 오인하지 않기 위한 같은 계약의 연속이다.
        for entry in root.iterdir():
            if entry == marker:
                continue
            if entry.is_dir() and _RUN_DIR_NAME.match(entry.name):
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    root.mkdir(parents=True, exist_ok=True)
    marker = root / _RUN_OUTPUT_OWNER_MARKER
    marker.write_text(_RUN_OUTPUT_OWNER_MARKER_CONTENT, encoding="utf-8")

    # 보존 개수 초과분 정리 — **전략별로** 센다. 이름의 시각 접두사가 같은(같은 초) run 은
    # 생성 시각으로 순서를 가른다.
    #
    # 종전에는 전략을 구분하지 않고 세어, 전략을 번갈아 돌리면 다른 전략의 기준 run 이
    # 밀려 나갔다 — 2026-08-08 에 실제로 framework 판정 기준 run 이 DBMS run 두 번에
    # 밀려 삭제됐고 저장 프롬프트(제한 replay 재료, 04 §5-1)를 잃었다. 이 보존 정책의
    # 선언된 목적이 "직전 run 이 남아야 백업 없이 전후 비교가 성립한다" 인 이상,
    # 비교 상대는 **같은 전략의 직전 run** 이다.
    retention = _run_retention()
    grouped: dict[str, list[Path]] = {}
    for entry in sorted(
        (entry for entry in root.iterdir()
         if entry.is_dir() and _RUN_DIR_NAME.match(entry.name)),
        key=lambda entry: (entry.name[:15], entry.stat().st_ctime_ns),
    ):
        grouped.setdefault(_run_strategy(entry), []).append(entry)
    incoming = getattr(strategy, "name", "") or _UNKNOWN_RUN_STRATEGY
    for name, runs in grouped.items():
        protected = {
            run for run in runs if _has_unrecovered_provider_evidence(run)
        }
        ordinary = [run for run in runs if run not in protected]
        # 새 run 몫 1개는 자기 전략 그룹에서만 비운다.
        keep = retention - 1 if name == incoming else retention
        for stale in ordinary[: max(0, len(ordinary) - keep)]:
            shutil.rmtree(stale)

    if run_dir is not None:
        resolved_run_dir = run_dir.resolve()
        if resolved_run_dir.parent != root:
            raise ValueError(
                f"run 폴더는 실행 결과 루트 바로 아래여야 합니다: {resolved_run_dir}"
            )
        resolved_run_dir.mkdir(parents=True, exist_ok=False)
        (resolved_run_dir / "llm-prompts").mkdir(parents=True, exist_ok=True)


def _resolved_neo4j_database() -> str:
    override = get_override()
    return (
        override.database
        if override is not None and override.database
        else SETTINGS.graph_database.database
    )


def _write_initial_manifest(
    run: AnalysisRun,
    sealed_analysis_inputs: SealedAnalysisInputs,
) -> None:
    """Write the run identity as a reference to the one complete immutable input seal."""
    source_root = run.codebase.root.resolve()
    corpus_root = run.codebase.corpus_root.resolve()
    ddl_root = run.codebase.ddl_dir.resolve() if run.codebase.ddl_dir else None
    manifest = {
        "schema_version": "2.0",
        "run_id": run.run_id,
        "analysis_scope_id": run.analysis_scope_id,
        "generation_id": run.run_id,
        "status": "initializing",
        "started_at": run.started_at.isoformat(),
        "corpus_root": str(corpus_root),
        "source_root": str(source_root),
        "selected_source_ids": list(run.codebase.selected_source_ids),
        "ddl_root": str(ddl_root) if ddl_root else None,
        "strategy": run.codebase.strategy,
        "locale": run.codebase.locale,
        "neo4j_database": _resolved_neo4j_database(),
        "run_output_root": str(run.run_dir.resolve()),
        "run_dir": str(run.run_dir.resolve()),
        "sealed_analysis_inputs": {
            "ref": str((run.run_dir / "sealed-analysis-inputs.json").resolve()),
            "sha256": sealed_analysis_inputs.sha256,
        },
    }
    manifest_path = run.run_dir / "manifest.json"
    with manifest_path.open("x", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _write_preflight_failure_manifest(run: AnalysisRun, error: BaseException) -> None:
    """Record an explicit failed preflight without pretending that inputs were sealed."""
    manifest = {
        "schema_version": "2.0",
        "run_id": run.run_id,
        "analysis_scope_id": run.analysis_scope_id,
        "generation_id": run.run_id,
        "status": "failed_preflight",
        "started_at": run.started_at.isoformat(),
        "strategy": run.codebase.strategy,
        "error": redact_sensitive_text(f"{type(error).__name__}: {error}"),
        "sealed_analysis_inputs": None,
    }
    target = run.run_dir / "manifest.json"
    with target.open("x", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def _write_source_ast_closure(
    run_dir: Path, closure: SourceAstClosure,
) -> tuple[Path, bytes, str]:
    """Persist the exact closed population before graph mutation."""
    target = run_dir / "source-ast-closure.json"
    temporary = target.with_suffix(".json.tmp")
    payload = (
        json.dumps(
            closure.as_dict(), ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n"
    ).encode("utf-8")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return target.resolve(), payload, hashlib.sha256(payload).hexdigest()


def _write_product_generation_closure(run_dir: Path, closure: dict[str, Any]) -> None:
    """Persist the complete verified product ledger after publication."""
    target = run_dir / "product-generation-closure.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(closure, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _update_run_manifest(
    run_dir: Path,
    *,
    status: str,
    graph_closure: dict[str, Any] | None = None,
) -> None:
    """Atomically add the terminal generation status to the run manifest."""
    target = run_dir / "manifest.json"
    manifest = json.loads(target.read_text(encoding="utf-8"))
    manifest["status"] = status
    manifest["generation_id"] = manifest["run_id"]
    if graph_closure is not None:
        manifest["product_closure"] = {
            key: {
                "count": graph_closure[key]["count"],
                "sha256": graph_closure[key]["sha256"],
            }
            for key in (
                "product_stages", "product_operations", "product_records",
            )
        }
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _summarize_usage(run_dir: Path) -> None:
    """``usage.jsonl`` → ``usage_summary.json`` 집계."""
    usage_path = run_dir / "usage.jsonl"
    if not usage_path.exists():
        return

    # ``response_cache_hits`` — 응답 캐시(spec 091)가 답을 돌려준 호출 수. 적중도 ``calls``
    # 에 포함되므로 이 값이 없으면 "호출은 늘었는데 토큰은 0" 으로 보여 집계가 오해를 만든다.
    per_agent_model: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
        "response_cache_hits": 0,
    })
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
        "response_cache_hits": 0,
    }

    with usage_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            key = (record.get("agent", "?"), record.get("model", "?"))
            bucket = per_agent_model[key]
            for field in ("input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
                bucket[field] += int(record.get(field, 0))
                total[field] += int(record.get(field, 0))
            bucket["cost_usd"] += float(record.get("cost_usd", 0.0))
            total["cost_usd"] += float(record.get("cost_usd", 0.0))
            bucket["calls"] += 1
            total["calls"] += 1
            if record.get("response_cache_hit"):
                bucket["response_cache_hits"] += 1
                total["response_cache_hits"] += 1

    summary = {
        "total": total,
        "per_agent_model": [
            {"agent": agent, "model": model, **stats}
            for (agent, model), stats in sorted(per_agent_model.items())
        ],
    }
    (run_dir / "usage_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 캐시가 일하는지 헛도는지 한 줄로 보인다 — 적중률이 뜻밖의 값이면 키가 깨진 것이다.
    calls = total["calls"]
    hits = total["response_cache_hits"]
    log_info(
        "llm.response_cache",
        "hit_rate",
        calls=calls,
        hits=hits,
        rate=f"{(hits / calls * 100) if calls else 0:.1f}%",
    )


def _summarize_analysis_findings(
    run_dir: Path,
    *,
    call_resolution_ledger: list[dict] | None = None,
    import_resolution_ledger: list[dict] | None = None,
) -> dict:
    """Strictly aggregate the append-only quality ledger for run-level visibility."""
    path = run_dir / "analysis-findings.jsonl"
    counts = {
        "total": 0,
        "blocking_total": 0,
        "by_status": {},
        "by_concern": {},
        "affected_targets": 0,
    }
    targets: set[str] = set()
    if path.exists():
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"invalid analysis finding evidence at {path}:{line_number}: {error}"
                    ) from error
                if not isinstance(record, dict):
                    raise ValueError(
                        f"invalid analysis finding evidence at {path}:{line_number}: object required"
                    )
                status = str(record.get("status") or "")
                concern = str(record.get("concern") or "")
                target = str(record.get("target_id") or "")
                if not status or not concern or not target:
                    raise ValueError(
                        f"invalid analysis finding evidence at {path}:{line_number}: "
                        "status/concern/target_id required"
                    )
                counts["total"] += 1
                if status != "excluded":
                    counts["blocking_total"] += 1
                counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
                counts["by_concern"][concern] = counts["by_concern"].get(concern, 0) + 1
                targets.add(target)
    counts["affected_targets"] = len(targets)
    call_resolution = summarize_call_resolution_ledger(
        call_resolution_ledger or []
    )
    counts["call_resolution"] = call_resolution
    import_resolution = summarize_import_resolution_ledger(
        import_resolution_ledger or []
    )
    counts["import_resolution"] = import_resolution
    counts["run_quality_status"] = (
        "completed_with_findings"
        if (
            counts["blocking_total"] or call_resolution["unresolved"]
            or import_resolution["unresolved"]
        )
        else "completed"
    )
    (run_dir / "analysis_findings_summary.json").write_text(
        json.dumps(counts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log_info(
        "run",
        "analysis_findings_summarized",
        total=counts["total"],
        blocking_total=counts["blocking_total"],
        status=counts["run_quality_status"],
        by_status=counts["by_status"] or None,
    )
    return counts
