"""Phase 1/2/3 통합 오케스트레이터.

Phase 1: AST 정적 그래프. 스캔 + Java AST + 아티팩트 파싱 + 인덱스 구축 + Neo4j 반영.
Phase 2: LLM 링킹. SQL→Table, Call batch, UML, analysis LLM.
Phase 3: 후처리. 요약 보강, Field/Class 요약, Table Description 보강.

기존 phase_ast / phase_llm / phase_post 를 하나의 thin 오케스트레이터 모듈로 통합.
"""

from __future__ import annotations

import logging
import os
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from util.ndjson_stream import emit_message, emit_data, emit_phase_event, PhaseTracker
from util.text_utils import read_text_with_fallback, read_json_with_fallback
from analyzer.strategy.framework.internal import (
    FrameworkAstProcessor,
    CLASS_TYPES,
    FIELD_TYPES,
    METHOD_TYPES,
    build_chunk_bundle_from_source,
    save_chunk_bundle,
    _get_chunk_bundle_dir_from_run_dir,
    run_step5,
    run_step6,
    derive_uses_from_calls,
    load_field_chunks_from_bundles,
    run_field_summary,
    run_class_summary,
)
from analyzer.strategy.framework.models import (
    FileCatalogEntry,
    ProcessingPlan,
    FieldFacts,
    MethodFacts,
    SqlStatementChunk,
    ConfigSectionChunk,
    ArtifactChunk,
    NODE_VARIABLE,
    NODE_METHOD_CALL,
    NODE_NEW_INSTANCE,
)
from analyzer.strategy.framework.graph.queries import (
    build_package_queries,
    build_class_queries,
    build_method_queries,
    build_field_queries,
    build_belongs_to_package_queries,
    build_has_method_queries,
    build_has_field_queries,
    _expand_package_fqns,
    build_package_hierarchy_queries,
    build_sql_statement_queries,
    build_config_section_queries,
    build_artifact_chunk_queries,
    build_refer_to_queries,
    build_reads_writes_queries,
    build_calls_queries,
    build_uses_queries,
    build_table_queries_from_reads_writes,
    build_column_queries,
    build_has_column_queries,
    build_method_table_from_writes_queries,
    build_class_table_refer_to_queries,
    build_artifact_table_refer_to_queries,
    build_artifact_class_refer_to_queries,
    build_method_summary_queries,
    build_uml_dependency_queries,
    build_field_summary_queries,
    build_class_summary_queries,
    build_table_description_enrichment_query,
    build_table_description_update_queries,
)
from analyzer.strategy.framework.graph.writer import run_graph_queries
from analyzer.strategy.framework.linking.deep_agent import run_deep_agent, run_deep_agent_entity_identification
from analyzer.strategy.framework.linking.matcher import has_external_entity_info

# Phase 1 에서 사용하는 함수들
from analyzer.strategy.framework.parsing.scanner import scan_project
from analyzer.strategy.framework.parsing.ast_walker import run_step2, _extract_package_fqns
from analyzer.strategy.framework.parsing.parsers.base import run_step3
from analyzer.strategy.framework.parsing.indexer import run_step4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 0: DDL (선택적 — DDL 파일 있을 때만)
# ---------------------------------------------------------------------------

async def run_phase_ddl(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 0: DDL 파일 처리 (선택적).

    DDL 파일이 업로드되어 있으면 테이블/컬럼/FK 스키마를 Neo4j에 생성.
    DDL 파일이 없으면 자연스럽게 스킵됨.
    """
    from analyzer.common.ddl_utils import list_ddl_files
    from analyzer.common.ddl_processor import process_ddl

    ddl_files = list_ddl_files(orchestrator)
    if not ddl_files:
        yield emit_message("📋 DDL 파일 없음 — DDL 처리 생략")
        return

    ddl_dir = orchestrator.dirs.get("ddl", "")
    ddl_schemas: set[str] = set()
    ddl_table_metadata: dict = {}
    total_files = len(ddl_files)
    datasource_name = getattr(orchestrator, "datasource_name", "")

    yield emit_message(f"📋 DDL 파일 {total_files}개 처리 시작")

    for idx, fname in enumerate(ddl_files):
        ddl_path = os.path.join(ddl_dir, fname)
        file_base = int((idx / total_files) * 100)
        file_end = int(((idx + 1) / total_files) * 100)

        yield emit_message(f"  📄 [{idx+1}/{total_files}] {fname}")
        try:
            async for chunk in process_ddl(
                ddl_path=ddl_path,
                client=client,
                file_name=fname,
                orchestrator=orchestrator,
                cypher_lock=pipeline._cypher_lock,
                ddl_schemas=ddl_schemas,
                ddl_table_metadata=ddl_table_metadata,
                emit_progress=True,
                file_base_progress=file_base,
                file_end_progress=file_end,
                datasource_name=datasource_name,
            ):
                if isinstance(chunk, bytes):
                    yield chunk
                else:
                    # (result, ddl_stats) 튜플 — 스트리밍에서 무시
                    pass
        except Exception as e:
            logger.warning("[FW:DDL] DDL 파일 처리 실패: %s — %s", fname, e)
            yield emit_message(f"  ⚠️ DDL 처리 실패: {fname} ({str(e)[:60]})")

    pipeline._pipeline_state["ddl_schemas"] = ddl_schemas
    pipeline._pipeline_state["ddl_table_metadata"] = ddl_table_metadata
    yield emit_message(f"✅ DDL 처리 완료: 스키마 {len(ddl_schemas)}개, 테이블 메타데이터 {len(ddl_table_metadata)}개")


# ---------------------------------------------------------------------------
# Phase 1: AST
# ---------------------------------------------------------------------------

async def run_phase_ast(
    pipeline: Any,
    file_names: list[tuple[str, str]],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 1: 스캔 → Java AST → 아티팩트 → 인덱스 구축.

    결과는 pipeline._pipeline_state에 저장되어 후속 Phase에서 사용.
    """
    data_dir = settings.path.data_dir
    source_dir = str(Path(data_dir) / "source")
    nontarget_dir = str(Path(data_dir) / "analysis" / "nontarget")

    yield emit_message("📂 프로젝트 스캔 시작")

    # --- Step 1: 스캔 ---
    lang = getattr(orchestrator, "target", "java") or "java"

    scan_roots = [(source_dir, "source"), (nontarget_dir, "analysis/nontarget")]
    catalog, plan = scan_project(scan_roots=scan_roots, lang=lang)
    logger.info("[FW:PHASE1] 스캔 완료 | catalog=%d source_ast=%d sql=%d entity=%d config=%d lang=%s", len(catalog), len(plan.source_ast_jobs), len(plan.sql_jobs), len(plan.entity_jobs), len(plan.config_jobs), lang)

    # --- Step 2: AST (파일별 점진적 스트리밍) ---
    yield emit_message(f"🔍 AST 파싱 ({len(plan.source_ast_jobs)}개 파일, lang={lang})")
    base = Path(data_dir).resolve()
    source_ast_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.source_ast_jobs]

    run_dir = pipeline._pipeline_state.get("run_dir")
    if not run_dir:
        run_dir = str(Path(settings.path.audit_dir) / "analysis.run")
        run_path = Path(run_dir)
        if run_path.is_dir():
            import shutil
            shutil.rmtree(run_path)
        run_path.mkdir(parents=True, exist_ok=True)
        pipeline._pipeline_state["run_dir"] = run_dir
        logger.info("[FW:PHASE1] run_dir 미설정 → 생성 | run_dir=%s (chunk_bundles는 data가 아닌 여기 저장)", run_dir)
    pipeline._pipeline_state["lang"] = lang

    # 해당 언어의 stdlib로 전환
    from analyzer.strategy.framework.linking.call_graph import set_active_stdlib
    set_active_stdlib(lang)

    # 파일별 누적 변수
    method_facts_list: list[MethodFacts] = []
    class_index_ast: dict[str, Any] = {}
    field_facts_list: list[FieldFacts] = []
    symbol_type_index: dict[str, dict[str, str]] = {}
    method_index: dict[str, str] = {}
    method_index_by_class: dict[str, dict[str, list[str]]] = {}
    class_index_simple: dict[str, list[str]] = {}
    ref_candidates_by_method: dict[str, list[dict[str, Any]]] = {}

    ast_total = len(source_ast_paths)
    ast_done = 0
    for file_result in run_step2(source_ast_paths, data_dir=data_dir, run_dir=run_dir, lang=lang):
        ast_done += 1
        facts = file_result["facts"]
        cidx = file_result["cidx"]
        fields = file_result["fields"]

        # 누적
        method_facts_list.extend(facts)
        class_index_ast.update(cidx)
        field_facts_list.extend(fields)
        for name, fqns in file_result["class_index_simple"].items():
            class_index_simple.setdefault(name, []).extend(fqns)
        for mid, types in file_result["sym_idx"].items():
            symbol_type_index.setdefault(mid, {}).update(types)
        method_index.update(file_result["meth_idx"])
        for cls, by_name in file_result["meth_by_cls"].items():
            for mname, ids in by_name.items():
                method_index_by_class.setdefault(cls, {}).setdefault(mname, []).extend(ids)
        for mid, cands in file_result["ref_cands"].items():
            ref_candidates_by_method.setdefault(mid, []).extend(cands)

        # 파일별 Neo4j 스트리밍
        if facts or cidx:
            try:
                queries: list[dict[str, Any]] = []
                queries.extend(build_class_queries(cidx))
                queries.extend(build_method_queries(facts))
                queries.extend(build_field_queries(fields))
                # NOTE: build_belongs_to_package_queries는 PACKAGE 노드 생성 후에 실행해야 함
                queries.extend(build_has_method_queries(facts, cidx))
                queries.extend(build_has_field_queries(fields, cidx))
                if queries:
                    async for graph in run_graph_queries(client, queries, label=f"PHASE1:AST:{file_result['file_name']}"):
                        yield emit_data(graph=graph)
            except Exception as e:
                logger.warning("[FW:PHASE1] Neo4j AST 반영 실패 (%s): %s", file_result["file_name"], e)
        # AST 파일별 진행률 (0~70% of phase, 나머지는 아티팩트/인덱스)
        ast_progress = int((ast_done / ast_total) * 70) if ast_total else 70
        yield emit_phase_event(0, "AST 정적 그래프", "in_progress", ast_progress,
                               {"completed": ast_done, "total": ast_total, "file": file_result["file_name"]})

    # 패키지 노드 (전체 class_index 누적 후)
    package_fqns = _extract_package_fqns(class_index_ast)
    if package_fqns:
        try:
            expanded_pkg = _expand_package_fqns(package_fqns)
            pkg_queries: list[dict[str, Any]] = []
            pkg_queries.extend(build_package_queries(expanded_pkg))
            pkg_queries.extend(build_package_hierarchy_queries(package_fqns))
            if pkg_queries:
                async for graph in run_graph_queries(client, pkg_queries, label="PHASE1:PACKAGE"):
                    yield emit_data(graph=graph)
        except Exception as e:
            logger.warning("[FW:PHASE1] Neo4j 패키지 반영 실패: %s", e)

    # CLASS/INTERFACE/ENUM → PACKAGE 관계 (PACKAGE 노드 생성 후에 실행)
    if class_index_ast:
        try:
            btp_queries = build_belongs_to_package_queries(class_index_ast)
            if btp_queries:
                async for graph in run_graph_queries(client, btp_queries, label="PHASE1:BELONGS_TO_PACKAGE"):
                    yield emit_data(graph=graph)
        except Exception as e:
            logger.warning("[FW:PHASE1] Neo4j CLASS→PACKAGE 관계 반영 실패: %s", e)

    stats.total_nodes += len(method_facts_list) + len(field_facts_list)
    yield emit_message(f"  └─ AST 완료 | 메서드 {len(method_facts_list)}개, 클래스 {len(class_index_ast)}개, 필드 {len(field_facts_list)}개")

    # --- Step 3: 아티팩트 ---
    sql_xml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.sql_jobs]
    entity_xml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.entity_jobs]
    config_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.config_jobs]

    yield emit_message(f"📄 아티팩트 파싱 | SQL {len(sql_xml_paths)}개, Entity {len(entity_xml_paths)}개, Config {len(config_paths)}개")
    sql_chunks, config_chunks, artifact_chunks = run_step3(
        sql_xml_paths, config_paths, [],
        xml_artifact_jobs=entity_xml_paths,
    )

    # Artifact breakdown 로그
    if artifact_chunks:
        from collections import Counter
        ct_counts = Counter(c.chunk_type for c in artifact_chunks)
        entity_count = sum(v for k, v in ct_counts.items() if k == "entity")
        breakdown = ", ".join(f"{k}={v}" for k, v in ct_counts.most_common())
        logger.info("[FW:PHASE1] artifact 전체=%d, entity→table 대상=%d | %s", len(artifact_chunks), entity_count, breakdown)

    # Neo4j: SqlStatement/Config/Artifact 노드 반영 (배치 단위 점진적 전송)
    if sql_chunks or config_chunks or artifact_chunks:
        try:
            queries = []
            queries.extend(build_sql_statement_queries(sql_chunks))
            queries.extend(build_config_section_queries(config_chunks))
            queries.extend(build_artifact_chunk_queries(artifact_chunks))
            if queries:
                async for graph in run_graph_queries(client, queries, label="PHASE1:ARTIFACT"):
                    yield emit_data(graph=graph)
        except Exception as e:
            logger.warning("[FW:PHASE1] Neo4j 아티팩트 반영 실패: %s", e)

    # --- Step 4: 인덱스 구축 ---
    sql_index, class_index, config_index, artifact_index = run_step4(
        sql_chunks, method_facts_list, class_index_ast, config_chunks, artifact_chunks=artifact_chunks,
    )

    # pipeline state에 저장
    pipeline._pipeline_state.update({
        "data_dir": data_dir,
        "catalog": catalog,
        "plan": plan,
        "method_facts_list": method_facts_list,
        "class_index_ast": class_index_ast,
        "package_fqns": package_fqns,
        "field_facts_list": field_facts_list,
        "symbol_type_index": symbol_type_index,
        "method_index": method_index,
        "method_index_by_class": method_index_by_class,
        "class_index_simple": class_index_simple,
        "ref_candidates_by_method": ref_candidates_by_method,
        "sql_chunks": sql_chunks,
        "config_chunks": config_chunks,
        "artifact_chunks": artifact_chunks,
        "sql_index": sql_index,
        "class_index": class_index,
        "config_index": config_index,
        "artifact_index": artifact_index,
    })

    yield emit_message(f"✅ Phase 1 완료 | SQL {len(sql_chunks)}개, 설정 {len(config_chunks)}개, 아티팩트 {len(artifact_chunks)}개")


# ---------------------------------------------------------------------------
# Phase 2: LLM 링킹
# ---------------------------------------------------------------------------

async def run_phase_llm(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2: 링킹 (SQL→Table LLM, Call batch, UML, analysis LLM)."""
    st = pipeline._pipeline_state
    method_facts_list = st["method_facts_list"]
    sql_index = st["sql_index"]
    sql_chunks = st["sql_chunks"]

    yield emit_message("🔗 링킹 시작 (참조 해석 → SQL→Table → Call → DeepAgent)")

    # PhaseTracker: LLM 링킹 단계 가중치 기반 진행률
    tracker = PhaseTracker(1, "LLM 링킹", total_phases=3)
    tracker.define_steps([
        ("결정론적 링킹", 10),       # step 0
        ("LLM 배치 링킹", 55),      # step 1 (table+call 통합) — tick per batch
        ("후처리", 10),              # step 2
        ("DeepAgent 의미 추적", 25), # step 3
    ])

    data_dir = st.get("data_dir") or settings.path.data_dir
    field_chunks = load_field_chunks_from_bundles(data_dir)

    # Step 5: 단계별 점진적 스트리밍
    refer_to_edges: list = []
    reads_writes: list = []
    fk_edges: list = []
    column_refs: list = []
    method_table_edges: list = []
    unresolved: list = []
    calls_edges_from_batch: list = []
    unresolved_calls: list = []
    table_descriptions: dict = {}
    class_table_edges: list = []
    artifact_table_edges: list = []
    all_calls: list = []
    uses_edges: list = []
    table_batch_count = 0
    call_batch_count = 0

    for step_result in run_step5(
        method_facts_list,
        sql_index,
        sql_chunks,
        config_index=st.get("config_index"),
        ref_candidates_by_method=st.get("ref_candidates_by_method"),
        symbol_type_index=st.get("symbol_type_index"),
        class_index=st.get("class_index"),
        field_chunks=field_chunks,
        data_dir=data_dir,
        field_facts_list=st.get("field_facts_list"),
        method_index_by_class=st.get("method_index_by_class"),
        run_dir=st.get("run_dir"),
        artifact_index=st.get("artifact_index"),
        artifact_chunks=st.get("artifact_chunks"),
        ddl_table_metadata=pipeline._pipeline_state.get("ddl_table_metadata"),
    ):
        stage = step_result["stage"]

        if stage == "deterministic":
            refer_to_edges = step_result["refer_to_edges"]
            unresolved = step_result["unresolved"]
            try:
                queries = build_refer_to_queries(refer_to_edges)
                if queries:
                    async for graph in run_graph_queries(client, queries, label="PHASE2:DETERMINISTIC"):
                        yield emit_data(graph=graph)
            except Exception as e:
                logger.warning("[FW:PHASE2] Neo4j 결정론적 링킹 반영 실패: %s", e)
            yield tracker.begin_step(0, 1)
            yield tracker.end_step(f"참조 {len(refer_to_edges)}건 연결, 미해결 {len(unresolved)}건")
            # LLM 배치 링킹 시작 (table_batch + call_batch 병렬)
            # 추정 배치 수: sql(20건씩) + call(20건씩)
            est_batches = max(1, (len(sql_chunks) + 19) // 20 + (len(method_facts_list) + 19) // 20)
            yield tracker.begin_step(1, est_batches)

        elif stage == "table_batch":
            batch_edges = step_result["edges"]
            batch_fk = step_result["fk"]
            batch_col = step_result["col"]
            batch_desc = step_result["desc"]
            reads_writes.extend(batch_edges)
            fk_edges.extend(batch_fk)
            column_refs.extend(batch_col)
            table_descriptions.update(batch_desc or {})
            table_batch_count += 1
            try:
                queries: list = []
                queries.extend(build_reads_writes_queries(batch_edges))
                queries.extend(build_table_queries_from_reads_writes(batch_edges, table_descriptions=batch_desc or {}))
                if batch_col:
                    queries.extend(build_column_queries(batch_col))
                    queries.extend(build_has_column_queries(batch_col))
                if queries:
                    async for graph in run_graph_queries(client, queries, label="PHASE2:TABLE_BATCH"):
                        yield emit_data(graph=graph)
            except Exception as e:
                logger.warning("[FW:PHASE2] Neo4j table 배치 반영 실패: %s", e)
            yield tracker.tick_quiet()

        elif stage == "call_batch":
            batch_call_edges = step_result["edges"]
            batch_call_unresolved = step_result["unresolved"]
            all_calls.extend(batch_call_edges)
            calls_edges_from_batch.extend(batch_call_edges)
            unresolved_calls.extend(batch_call_unresolved)
            uses_batch = derive_uses_from_calls(batch_call_edges, method_facts_list)
            uses_edges.extend(uses_batch)
            call_batch_count += 1
            try:
                queries = []
                queries.extend(build_calls_queries(batch_call_edges))
                queries.extend(build_uses_queries(uses_batch))
                if queries:
                    async for graph in run_graph_queries(client, queries, label="PHASE2:CALL_BATCH"):
                        yield emit_data(graph=graph)
            except Exception as e:
                logger.warning("[FW:PHASE2] Neo4j call 배치 반영 실패: %s", e)
            yield tracker.tick_quiet()

        elif stage == "post":
            yield tracker.end_step(f"Table LLM {table_batch_count}건, Call LLM {call_batch_count}건 완료")
            reads_writes = step_result["reads_writes"]
            fk_edges = step_result["fk_edges"]
            column_refs = step_result["column_refs"]
            method_table_edges = step_result["method_table_edges"]
            class_table_edges = step_result["class_table_edges"]
            artifact_table_edges = step_result["artifact_table_edges"]
            try:
                queries = []
                queries.extend(build_method_table_from_writes_queries(method_table_edges))
                queries.extend(build_class_table_refer_to_queries(class_table_edges))
                queries.extend(build_artifact_table_refer_to_queries(artifact_table_edges))
                # ARTIFACT_CHUNK → CLASS 연결 (EJB XML의 ejb-class/home/remote FQN 기반)
                artifact_chunks_from_state = st.get("artifact_chunks") or []
                class_index_from_state = st.get("class_index") or {}
                artifact_class_rels = build_artifact_class_refer_to_queries(artifact_chunks_from_state, class_index_from_state)
                queries.extend(artifact_class_rels)
                if queries:
                    async for graph in run_graph_queries(client, queries, label="PHASE2:POST"):
                        yield emit_data(graph=graph)
            except Exception as e:
                logger.warning("[FW:PHASE2] Neo4j 후처리 반영 실패: %s", e)
            yield tracker.begin_step(2, 1)
            artifact_class_count = len(artifact_class_rels) if 'artifact_class_rels' in dir() else 0
            yield tracker.end_step(f"Method→Table {len(method_table_edges)}건, Class→Table {len(class_table_edges)}건, Artifact→Table {len(artifact_table_edges)}건, Artifact→Class {artifact_class_count}건")

    # DeepAgent 엔티티 식별 — 시나리오별 분기
    # 시나리오 1 (DDL + 외부파일): 현행 결정론 매칭으로 충분 → 스킵
    # 시나리오 2 (DDL만): 테이블명 컨텍스트 + 코드 추적 → DeepAgent 실행
    # 시나리오 3 (외부파일만): 현행 table_extract LLM으로 충분 → 스킵
    # 시나리오 4 (아무것도 없음): 후보 기반 엔티티 식별 → DeepAgent 실행
    ddl_meta = pipeline._pipeline_state.get("ddl_table_metadata") or {}
    _has_external_files = has_external_entity_info(
        artifact_index=st.get("artifact_index"),
        class_index=st.get("class_index"),
    )
    _needs_deep_entity = (
        (bool(ddl_meta) and not _has_external_files)  # 시나리오 2: DDL만
        or (not ddl_meta and not _has_external_files)  # 시나리오 4: 아무것도 없음
    )

    if _needs_deep_entity:
        scenario = "ddl_only" if ddl_meta else "no_external"
        logger.info("[FW:PHASE2] DeepAgent 엔티티 식별 실행 (scenario=%s)", scenario)
        entity_api_key = settings.llm.api_key
        entity_run_dir = Path(st["run_dir"]) if st.get("run_dir") else None
        entity_state = {
            "sql_index": sql_index,
            "config_index": st.get("config_index") or {},
            "artifact_index": st.get("artifact_index") or {},
            "data_dir": data_dir,
            "run_dir": str(entity_run_dir) if entity_run_dir else None,
            "span_index": st.get("span_index"),
            "chunk_index": st.get("chunk_index"),
        }
        entity_mt_edges = run_deep_agent_entity_identification(
            st.get("class_index") or {},
            entity_state,
            api_key=entity_api_key,
            run_dir=entity_run_dir,
            ddl_table_metadata=ddl_meta or None,
        )
        if entity_mt_edges:
            method_table_edges.extend(entity_mt_edges)
            logger.info("[FW:PHASE2] DeepAgent 엔티티 식별 완료 | Method→Table %d건", len(entity_mt_edges))
            try:
                eid_queries = build_method_table_from_writes_queries(entity_mt_edges)
                if eid_queries:
                    async for graph in run_graph_queries(client, eid_queries, label="PHASE2:ENTITY_ID"):
                        yield emit_data(graph=graph)
            except Exception as e:
                logger.warning("[FW:PHASE2] Neo4j 엔티티 식별 반영 실패: %s", e)
        # bundle index를 state에 보존 (후속 DeepAgent 재사용)
        if entity_state.get("span_index"):
            st.setdefault("span_index", entity_state["span_index"])
            st.setdefault("chunk_index", entity_state["chunk_index"])
    else:
        logger.info("[FW:PHASE2] 외부 엔티티 정보 충분 — DeepAgent 엔티티 식별 스킵")

    # DeepAgent: 항목별 점진적 스트리밍
    if not unresolved:
        yield tracker.begin_step(3, 1)
        yield tracker.end_step("미해결 0건 (건너뜀)")
    else:
        yield tracker.begin_step(3, len(unresolved))
        api_key = settings.llm.api_key
        run_dir_path = Path(st["run_dir"]) if st.get("run_dir") else None
        deep_state = {
            "sql_index": sql_index,
            "config_index": st.get("config_index") or {},
            "artifact_index": st.get("artifact_index") or {},
            "data_dir": data_dir,
            "run_dir": str(run_dir_path) if run_dir_path else None,
            "span_index": st.get("span_index"),
            "chunk_index": st.get("chunk_index"),
        }
        da_refer_all: list = []
        da_mt_all: list = []
        for item_refer, item_mt in run_deep_agent(
            unresolved,
            deep_state,
            api_key=api_key,
            run_dir=run_dir_path,
        ):
            da_refer_all.extend(item_refer)
            da_mt_all.extend(item_mt)
            # 항목별 Neo4j 스트리밍
            try:
                da_queries: list = []
                if item_refer:
                    da_queries.extend(build_refer_to_queries(item_refer))
                if item_mt:
                    da_queries.extend(build_method_table_from_writes_queries(item_mt))
                if da_queries:
                    async for graph in run_graph_queries(client, da_queries, label="PHASE2:DEEPAGENT"):
                        yield emit_data(graph=graph)
            except Exception as e:
                logger.warning("[FW:PHASE2] Neo4j DeepAgent 항목 반영 실패: %s", e)

        if da_refer_all:
            refer_to_edges.extend(da_refer_all)
            logger.info("[FW:PHASE2] DeepAgent REFER_TO 추가 | count=%d", len(da_refer_all))
        if da_mt_all:
            method_table_edges.extend(da_mt_all)
            logger.info("[FW:PHASE2] DeepAgent Method→Table 직접 추가 | count=%d", len(da_mt_all))
        # DeepAgent refer_to에서 Method→ArtifactChunk→Table 체인으로 Method→Table 자동 유도
        if da_refer_all and artifact_table_edges:
            from analyzer.strategy.framework.linking.orchestrator import derive_method_table_edges
            da_derived_mt = derive_method_table_edges(da_refer_all, reads_writes, artifact_table_edges=artifact_table_edges)
            if da_derived_mt:
                method_table_edges.extend(da_derived_mt)
                logger.info("[FW:PHASE2] DeepAgent Method→Table 유도 | count=%d", len(da_derived_mt))
                try:
                    derived_queries = build_method_table_from_writes_queries(da_derived_mt)
                    if derived_queries:
                        async for graph in run_graph_queries(client, derived_queries, label="PHASE2:DEEPAGENT_DERIVED"):
                            yield emit_data(graph=graph)
                except Exception as e:
                    logger.warning("[FW:PHASE2] Neo4j DeepAgent 유도 반영 실패: %s", e)
        # deepagent 가 빌드한 index 를 pipeline state 에 보존
        if deep_state.get("span_index"):
            st.setdefault("span_index", deep_state["span_index"])
            st.setdefault("chunk_index", deep_state["chunk_index"])
        yield tracker.end_step(f"REFER_TO {len(da_refer_all)}건, Method→Table {len(da_mt_all)}건 추가")

    stats.total_nodes += len(refer_to_edges) + len(all_calls)
    st.update({
        "refer_to_edges": refer_to_edges,
        "reads_writes": reads_writes,
        "fk_edges": fk_edges,
        "column_refs": column_refs,
        "method_table_edges": method_table_edges,
        "class_table_edges": class_table_edges,
        "unresolved": unresolved,
        "calls_edges_from_batch": calls_edges_from_batch,
        "unresolved_calls": unresolved_calls,
        "calls_edges": all_calls,
        "uses_edges": uses_edges,
        "table_descriptions": table_descriptions,
    })

    yield emit_message(f"✅ Phase 2 완료 | 참조 {len(refer_to_edges)}건, CALLS {len(all_calls)}건, Table배치 {table_batch_count}건, Call배치 {call_batch_count}건")


# ---------------------------------------------------------------------------
# Phase 3: 후처리
# ---------------------------------------------------------------------------

async def run_phase_post(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 메서드 요약, Field/Class 요약, Table Description 보강."""
    st = pipeline._pipeline_state
    method_facts_list = st.get("method_facts_list") or []
    field_facts_list = st.get("field_facts_list") or []
    class_index = st.get("class_index") or {}
    refer_to_edges = st.get("refer_to_edges") or []
    reads_writes = st.get("reads_writes") or []
    all_calls = st.get("calls_edges") or []
    sql_index = st.get("sql_index") or {}
    api_key = settings.llm.api_key
    locale = settings.locale

    # PhaseTracker: 후처리 단계 가중치 기반 진행률
    tracker = PhaseTracker(2, "후처리", total_phases=3)
    tracker.define_steps([
        ("메서드 요약", 35),        # step 0
        ("Field 요약", 15),         # step 1
        ("Class 요약", 25),         # step 2
        ("Table Description 보강", 25),  # step 3
    ])

    # --- 메서드 요약 (Step 6: analysis LLM — 20개씩 배치 점진적 스트리밍) ---
    total_summary_batches = max(1, (len(method_facts_list) + 19) // 20)
    yield tracker.begin_step(0, total_summary_batches)

    summary_by_method: dict = {}
    for summary_batch in run_step6(
        method_facts_list,
        refer_to_edges,
        reads_writes,
        use_llm=bool(api_key),
        api_key=api_key,
        locale=locale,
        parallel=True,
        class_index=class_index,
        sql_index=sql_index,
        config_index=st.get("config_index"),
        calls_edges=all_calls,
        field_facts_list=field_facts_list,
        lang=st.get("lang", "java"),
    ):
        summary_by_method.update(summary_batch)
        yield tracker.tick_quiet()
        try:
            queries = []
            queries.extend(build_method_summary_queries(summary_batch))
            queries.extend(
                build_uml_dependency_queries(
                    summary_batch,
                    method_facts_list,
                    class_index,
                )
            )
            if queries:
                async for graph in run_graph_queries(client, queries, label="PHASE3:SUMMARY"):
                    yield emit_data(graph=graph)
        except Exception as e:
            logger.warning("[FW:PHASE3] Neo4j summary 배치 반영 실패: %s", e)

    st["summary_by_method"] = summary_by_method
    yield tracker.end_step(f"메서드 {len(summary_by_method)}개 요약 완료")

    # --- Field 요약 (완료 즉시 스트리밍) ---
    field_total = max(1, len(field_facts_list))
    yield tracker.begin_step(1, field_total)
    summary_by_field: Dict[str, Any] = {}
    for field_batch in run_field_summary(
        field_facts_list, class_index,
        data_dir=st.get("data_dir", ""),
        run_dir=st.get("run_dir"),
        api_key=api_key, locale=locale,
    ):
        summary_by_field.update(field_batch)
        yield tracker.tick_quiet(len(field_batch))
        try:
            queries = build_field_summary_queries(field_batch)
            if queries:
                async for graph in run_graph_queries(client, queries, label="PHASE3:FIELD_SUMMARY"):
                    yield emit_data(graph=graph)
        except Exception as e:
            logger.warning("[FW:PHASE3] Neo4j field summary 반영 실패: %s", e)

    yield tracker.end_step(f"필드 {len(summary_by_field)}개 요약 완료")

    # --- Class 요약 (클래스별 스트리밍) ---
    class_total = max(1, len(class_index))
    yield tracker.begin_step(2, class_total)
    summary_by_class: Dict[str, Any] = {}
    for class_batch in run_class_summary(
        class_index, summary_by_method, summary_by_field,
        method_facts_list, field_facts_list,
        api_key=api_key, locale=locale,
    ):
        summary_by_class.update(class_batch)
        yield tracker.tick_quiet(len(class_batch))
        try:
            queries = build_class_summary_queries(class_batch)
            if queries:
                async for graph in run_graph_queries(client, queries, label="PHASE3:CLASS_SUMMARY"):
                    yield emit_data(graph=graph)
        except Exception as e:
            logger.warning("[FW:PHASE3] Neo4j class summary 반영 실패: %s", e)

    yield tracker.end_step(f"클래스 {len(summary_by_class)}개 요약 완료")

    # --- Table Description 보강 (메서드 summary 기반) ---
    yield tracker.begin_step(3, 1)
    enriched_table_count = 0
    if api_key and summary_by_method:
        try:
            enrichment_query = build_table_description_enrichment_query()
            table_results = await client.execute_queries([enrichment_query])
            tables_with_summaries = table_results[0] if table_results and table_results[0] else []

            if tables_with_summaries:
                yield emit_message(f"  ├─ {len(tables_with_summaries)}개 테이블 LLM 설명 보강 중...")
                from concurrent.futures import ThreadPoolExecutor, as_completed
                from util.prompt_executor import RuleLoader

                loader = RuleLoader(target_lang="dbms")

                def _enrich_one_table(table_info: dict) -> Optional[Dict[str, Any]]:
                    name = table_info.get("name", "")
                    schema = table_info.get("schema", "")
                    current_desc = table_info.get("current_description") or ""
                    method_summaries = table_info.get("method_summaries") or []
                    table_display = f"{schema}.{name}" if schema else name
                    # 메서드 summary를 table_sentences로 전달
                    sentences = [f"[메서드 요약] {s}" for s in method_summaries[:20]]
                    if current_desc:
                        sentences.insert(0, f"[기존 설명] {current_desc}")
                    result = loader.execute(
                        "table_summary",
                        {
                            "table_name": table_display,
                            "table_sentences": sentences,
                            "column_sentences": {},
                            "column_metadata": {},
                            "locale": locale,
                        },
                        api_key=api_key,
                    )
                    if isinstance(result, dict) and result.get("tableDescription"):
                        return {
                            "db": table_info.get("db", ""),
                            "schema": schema,
                            "name": name,
                            "enriched_description": result["tableDescription"],
                        }
                    return None

                workers = max(1, settings.concurrency.max_concurrency)
                enrichments: List[Dict[str, Any]] = []
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(_enrich_one_table, t): t for t in tables_with_summaries}
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                            if result:
                                enrichments.append(result)
                        except Exception as e:
                            t = futures[future]
                            logger.warning("[FW:PHASE3] Table description 보강 실패 (%s): %s", t.get("name", ""), e)

                if enrichments:
                    enriched_table_count = len(enrichments)
                    try:
                        queries = build_table_description_update_queries(enrichments)
                        if queries:
                            async for graph in run_graph_queries(client, queries, label="PHASE3:TABLE_ENRICHMENT"):
                                yield emit_data(graph=graph)
                    except Exception as e:
                        logger.warning("[FW:PHASE3] Neo4j table description 보강 반영 실패: %s", e)
        except Exception as e:
            logger.warning("[FW:PHASE3] Table description 보강 스킵: %s", e)

    yield tracker.end_step(f"테이블 {enriched_table_count}개 설명 보강")

    st.update({
        "summary_by_field": summary_by_field,
        "summary_by_class": summary_by_class,
    })

    yield emit_message(
        f"✅ Phase 3 완료 | 메서드요약 {len(summary_by_method)}개, "
        f"필드요약 {len(summary_by_field)}개, 클래스요약 {len(summary_by_class)}개, "
        f"테이블보강 {enriched_table_count}개"
    )
