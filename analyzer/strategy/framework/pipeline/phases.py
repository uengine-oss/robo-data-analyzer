"""Phase 1/2/3 통합 오케스트레이터.

Phase 1: AST 정적 그래프. 스캔 + Java AST + 아티팩트 파싱 + 인덱스 구축 + Neo4j 반영.
Phase 2: LLM 링킹. SQL→Table, Call batch, UML, analysis LLM.
Phase 3: 후처리. 요약 보강, Field/Class 요약, User Story 생성.

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
from util.ndjson_stream import emit_message, emit_data
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
    run_user_story_generation,
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
    build_method_summary_queries,
    build_uml_dependency_queries,
    build_field_summary_queries,
    build_class_summary_queries,
    build_user_story_queries,
)
from analyzer.strategy.framework.graph.writer import run_graph_queries
from analyzer.strategy.framework.linking.deep_agent import run_deep_agent

# Phase 1 에서 사용하는 함수들
from analyzer.strategy.framework.parsing.scanner import scan_project
from analyzer.strategy.framework.parsing.ast_walker import run_step2
from analyzer.strategy.framework.parsing.parsers.base import run_step3
from analyzer.strategy.framework.parsing.indexer import run_step4

logger = logging.getLogger(__name__)


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

    yield emit_message("📂 Step 1: 프로젝트 스캔")

    # --- Step 1: 스캔 ---
    scan_roots = [(source_dir, "source"), (nontarget_dir, "analysis/nontarget")]
    catalog, plan = scan_project(scan_roots=scan_roots)
    logger.info("[FW:PHASE1] 스캔 완료 | catalog=%d java_ast=%d xml_sqlmap=%d", len(catalog), len(plan.java_ast_jobs), len(plan.xml_sqlmap_jobs))

    # --- Step 2: Java AST ---
    yield emit_message(f"🔍 Step 2: Java AST 파싱 ({len(plan.java_ast_jobs)} 파일)")
    base = Path(data_dir).resolve()
    java_ast_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.java_ast_jobs]

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
    (
        method_facts_list, class_index_ast, package_fqns,
        field_facts_list, symbol_type_index, method_index,
        method_index_by_class, class_index_simple, ref_candidates_by_method,
    ) = run_step2(java_ast_paths, data_dir=data_dir, run_dir=run_dir)

    # Neo4j: Package/Class/Method/Field 노드 반영
    if method_facts_list or class_index_ast:
        try:
            queries = []
            expanded_pkg = _expand_package_fqns(package_fqns)
            queries.extend(build_package_queries(expanded_pkg))
            queries.extend(build_package_hierarchy_queries(package_fqns))
            queries.extend(build_class_queries(class_index_ast))
            queries.extend(build_method_queries(method_facts_list))
            queries.extend(build_field_queries(field_facts_list))
            queries.extend(build_belongs_to_package_queries(class_index_ast))
            queries.extend(build_has_method_queries(method_facts_list, class_index_ast))
            queries.extend(build_has_field_queries(field_facts_list, class_index_ast))
            if queries:
                await run_graph_queries(client, queries, label="PHASE1:AST")
        except Exception as e:
            logger.warning("[FW:PHASE1] Neo4j AST 반영 실패: %s", e)

    stats.total_nodes += len(method_facts_list) + len(field_facts_list)
    yield emit_message(f"✅ AST 완료 | methods={len(method_facts_list)} classes={len(class_index_ast)} fields={len(field_facts_list)}")

    # --- Step 3: 아티팩트 ---
    xml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.xml_sqlmap_jobs]
    yaml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.yaml_jobs]
    prop_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.properties_jobs]
    xml_ejb_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.xml_ejb_jobs]
    xml_entitymodel_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.xml_entitymodel_jobs]

    yield emit_message(f"📄 Step 3: 아티팩트 파싱 | xml={len(xml_paths)} yaml={len(yaml_paths)}")
    sql_chunks, config_chunks, artifact_chunks = run_step3(
        xml_paths, yaml_paths, prop_paths,
        xml_ejb_jobs=xml_ejb_paths, xml_entitymodel_jobs=xml_entitymodel_paths,
    )

    # Neo4j: SqlStatement/Config/Artifact 노드 반영
    if sql_chunks or config_chunks or artifact_chunks:
        try:
            queries = []
            queries.extend(build_sql_statement_queries(sql_chunks))
            queries.extend(build_config_section_queries(config_chunks))
            queries.extend(build_artifact_chunk_queries(artifact_chunks))
            if queries:
                await run_graph_queries(client, queries, label="PHASE1:ARTIFACT")
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

    yield emit_message(f"✅ Phase 1 완료 | sql={len(sql_chunks)} config={len(config_chunks)}")


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

    yield emit_message("🔗 Step 5: 링킹 시작 (SQL→Table, Call, UML)")

    data_dir = st.get("data_dir") or settings.path.data_dir
    field_chunks = load_field_chunks_from_bundles(data_dir)

    (
        refer_to_edges,
        reads_writes,
        fk_edges,
        column_refs,
        method_table_edges,
        unresolved,
        calls_edges_from_batch,
        unresolved_calls,
        table_descriptions,
        class_table_edges,
        artifact_table_edges,
    ) = run_step5(
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
    )

    all_calls = list(calls_edges_from_batch or [])
    uses_edges = derive_uses_from_calls(all_calls, method_facts_list)

    # Neo4j: 결정론적 결과 먼저 반영 (DeepAgent 전에)
    try:
        queries = []
        queries.extend(build_refer_to_queries(refer_to_edges))
        queries.extend(build_reads_writes_queries(reads_writes))
        queries.extend(build_table_queries_from_reads_writes(reads_writes, table_descriptions=table_descriptions or {}))
        if column_refs:
            queries.extend(build_column_queries(column_refs))
            queries.extend(build_has_column_queries(column_refs))
        queries.extend(build_method_table_from_writes_queries(method_table_edges))
        queries.extend(build_class_table_refer_to_queries(class_table_edges))
        queries.extend(build_artifact_table_refer_to_queries(artifact_table_edges))
        queries.extend(build_calls_queries(all_calls))
        queries.extend(build_uses_queries(uses_edges))
        if queries:
            await run_graph_queries(client, queries, label="PHASE2:LINKING")
    except Exception as e:
        logger.warning("[FW:PHASE2] Neo4j 결정론적 링킹 반영 실패: %s", e)

    yield emit_message(f"🔗 결정론적 링킹 완료 | refer_to={len(refer_to_edges)} class_table={len(class_table_edges)} artifact_table={len(artifact_table_edges)} calls={len(all_calls)} uses={len(uses_edges)}")

    # DeepAgent: 인덱스 키 매칭 실패 항목만 의미 기반 추적
    # unresolved 는 link_chunks_to_indexes 에서 나온 것만 포함 (CALL_REF/NEW_INSTANCE_REF 실패는 이미 제외됨)
    if unresolved:
        yield emit_message(f"🔍 DeepAgent: 미해결 {len(unresolved)}건 의미 추적 시작")
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
        da_refer, da_mt = run_deep_agent(
            unresolved,
            deep_state,
            api_key=api_key,
            run_dir=run_dir_path,
        )
        if da_refer:
            refer_to_edges.extend(da_refer)
            logger.info("[FW:PHASE2] DeepAgent REFER_TO 추가 | count=%d", len(da_refer))
        if da_mt:
            method_table_edges.extend(da_mt)
            logger.info("[FW:PHASE2] DeepAgent Method→Table 직접 추가 | count=%d", len(da_mt))
        # DeepAgent refer_to에서 Method→ArtifactChunk→Table 체인으로 Method→Table 자동 유도
        if da_refer and artifact_table_edges:
            from analyzer.strategy.framework.linking.orchestrator import derive_method_table_edges
            da_derived_mt = derive_method_table_edges(da_refer, reads_writes, artifact_table_edges=artifact_table_edges)
            if da_derived_mt:
                method_table_edges.extend(da_derived_mt)
                da_mt.extend(da_derived_mt)  # Neo4j 반영용
                logger.info("[FW:PHASE2] DeepAgent Method→Table 유도 | count=%d", len(da_derived_mt))
        # deepagent 가 빌드한 index 를 pipeline state 에 보존
        if deep_state.get("span_index"):
            st.setdefault("span_index", deep_state["span_index"])
            st.setdefault("chunk_index", deep_state["chunk_index"])
        # DeepAgent 결과를 Neo4j에 추가 반영
        try:
            da_queries = []
            if da_refer:
                da_queries.extend(build_refer_to_queries(da_refer))
            if da_mt:
                da_queries.extend(build_method_table_from_writes_queries(da_mt))
            if da_queries:
                await run_graph_queries(client, da_queries, label="PHASE2:DEEPAGENT")
        except Exception as e:
            logger.warning("[FW:PHASE2] Neo4j DeepAgent 결과 반영 실패: %s", e)
        yield emit_message(f"🔍 DeepAgent 완료 | refer_to={len(da_refer)} method_table={len(da_mt)}")

    # analysis LLM (method summary + stereotype)
    yield emit_message("🤖 Step 6: analysis LLM (메서드 요약)")
    api_key = settings.llm.api_key
    locale = settings.locale

    summary_by_method = run_step6(
        method_facts_list,
        refer_to_edges,
        reads_writes,
        use_llm=bool(api_key),
        api_key=api_key,
        locale=locale,
        parallel=True,
        class_index=st.get("class_index"),
        sql_index=sql_index,
        config_index=st.get("config_index"),
        calls_edges=all_calls,
    )

    # Neo4j: method summary 반영
    if summary_by_method:
        try:
            queries = []
            queries.extend(build_method_summary_queries(summary_by_method))
            queries.extend(
                build_uml_dependency_queries(
                    summary_by_method,
                    method_facts_list,
                    st.get("class_index") or {},
                )
            )
            if queries:
                await run_graph_queries(client, queries, label="PHASE2:SUMMARY")
        except Exception as e:
            logger.warning("[FW:PHASE2] Neo4j summary 반영 실패: %s", e)

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
        "summary_by_method": summary_by_method,
        "table_descriptions": table_descriptions,
    })

    yield emit_message(f"✅ Phase 2 완료 | summary={len(summary_by_method)}")


# ---------------------------------------------------------------------------
# Phase 3: 후처리
# ---------------------------------------------------------------------------

async def run_phase_post(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 호출 그래프 기반 요약 보강, Field/Class 요약, User Story."""
    st = pipeline._pipeline_state
    summary_by_method = st.get("summary_by_method") or {}
    method_facts_list = st.get("method_facts_list") or []
    field_facts_list = st.get("field_facts_list") or []
    class_index = st.get("class_index") or {}
    api_key = settings.llm.api_key
    locale = settings.locale

    # --- Field 요약 ---
    yield emit_message("📝 Field 요약")
    summary_by_field = run_field_summary(
        field_facts_list, class_index,
        data_dir=st.get("data_dir", ""),
        api_key=api_key, locale=locale,
    )
    if summary_by_field:
        try:
            queries = build_field_summary_queries(summary_by_field)
            if queries:
                await run_graph_queries(client, queries, label="PHASE3:FIELD_SUMMARY")
        except Exception as e:
            logger.warning("[FW:PHASE3] Neo4j field summary 반영 실패: %s", e)

    # --- Class 요약 ---
    yield emit_message("📋 Class 요약")
    summary_by_class = run_class_summary(
        class_index, summary_by_method, summary_by_field,
        method_facts_list, field_facts_list,
        api_key=api_key, locale=locale,
    )
    if summary_by_class:
        try:
            queries = build_class_summary_queries(summary_by_class)
            if queries:
                await run_graph_queries(client, queries, label="PHASE3:CLASS_SUMMARY")
        except Exception as e:
            logger.warning("[FW:PHASE3] Neo4j class summary 반영 실패: %s", e)

    # --- User Story ---
    yield emit_message("📖 User Story 생성")
    user_stories: List[Dict[str, Any]] = []
    try:
        user_stories = run_user_story_generation(
            summary_by_class or {}, class_index,
            api_key=api_key, locale=locale,
        )
        if user_stories:
            try:
                queries = build_user_story_queries(user_stories)
                if queries:
                    await run_graph_queries(client, queries, label="PHASE3:USER_STORY")
            except Exception as e:
                logger.warning("[FW:PHASE3] Neo4j user story 반영 실패: %s", e)
    except Exception as e:
        logger.warning("[FW:PHASE3] User story 생성 실패: %s", e)

    st.update({
        "summary_by_field": summary_by_field,
        "summary_by_class": summary_by_class,
        "user_stories": user_stories,
    })

    yield emit_message(f"✅ Phase 3 완료 | field_summary={len(summary_by_field)} class_summary={len(summary_by_class)} user_stories={len(user_stories)}")
