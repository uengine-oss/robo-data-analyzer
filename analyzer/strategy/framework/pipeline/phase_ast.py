"""Phase 1: AST 정적 그래프.

스캔 → AST 파싱 → 아티팩트 파싱 → 인덱스 구축 → Neo4j 반영.
결과는 ctx.state(FrameworkPipelineState)에 저장되어 후속 Phase에서 사용.
"""

from __future__ import annotations

import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, AsyncGenerator

from config.settings import settings
from util.stream_emitter import emit_message, emit_phase_event
from analyzer.strategy.framework.linking.call_linker import set_active_stdlib
from analyzer.strategy.framework.common.models.node_models import (
    VariableFacts,
    FunctionFacts,
)
from analyzer.strategy.framework.common.cypher.graph_queries import (
    build_package_queries,
    build_module_queries,
    build_function_queries,
    build_variable_queries,
    build_belongs_to_package_queries,
    build_root_package_queries,
    build_has_function_queries,
    build_has_variable_queries,
    _expand_package_fqns,
    build_package_hierarchy_queries,
    build_sql_statement_queries,
    build_config_section_queries,
    build_artifact_chunk_queries,
)
from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.strategy.framework.analysis.ast_collector import extract_code_structure, _extract_package_fqns
from analyzer.strategy.framework.analysis.parsers.base import parse_config_and_artifacts
from analyzer.strategy.framework.analysis.index_builder import build_search_indexes
from analyzer.strategy.framework.scanning import profile as build_codebase_profile

logger = logging.getLogger(__name__)


async def run_phase_ast(
    ctx: Any,  # FrameworkPhaseContext
    file_names: list[tuple[str, str]],
) -> AsyncGenerator[bytes, None]:
    """Phase 1: 스캔 → AST 파싱 → 아티팩트 → 인덱스 구축."""
    client = ctx.client
    stats = ctx.stats
    st = ctx.state
    data_dir = settings.path.data_dir
    source_dir = str(Path(data_dir) / "source")

    yield emit_message("📂 코드베이스 프로파일 + 스캔 시작")

    # ── Step 1: 프로파일 (스캔 + 결정론 수집 + 프레임워크/DB 패턴 Agent 요약) ──
    # 모든 파일이 source/ 에 통합되어 있으므로 scan_roots 는 source 하나만.
    lang = ctx.orchestrator.target
    scan_roots = [(source_dir, "source")]
    codebase_profile = build_codebase_profile(
        Path(source_dir),
        lang=lang,
        scan_roots=scan_roots,
        api_key=settings.llm.api_key or "",
    )
    st.codebase_profile = codebase_profile
    catalog = codebase_profile.catalog
    plan = codebase_profile.plan

    # ── Step 2: AST 파싱 (파일별 점진적 스트리밍) ──
    yield emit_message(f"🔍 AST 파싱 ({len(plan.source_ast_jobs)}개 파일, lang={lang})")
    base = Path(data_dir).resolve()
    source_ast_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.source_ast_jobs]

    run_dir = st.run_dir
    if not run_dir:
        run_dir = str(Path(settings.path.audit_dir) / "analysis.run")
        run_path = Path(run_dir)
        if run_path.is_dir():
            shutil.rmtree(run_path)
        run_path.mkdir(parents=True, exist_ok=True)
        st.run_dir = run_dir
        logger.info("[PHASE:1:AST] run_dir 생성 | run_dir=%s", run_dir)
    st.lang = lang

    # 해당 언어의 stdlib로 전환
    set_active_stdlib(lang)

    # 파일별 누적 변수
    function_facts_list: list[FunctionFacts] = []
    module_index_ast: dict[str, Any] = {}
    variable_facts_list: list[VariableFacts] = []
    symbol_type_index: dict[str, dict[str, str]] = {}
    function_index: dict[str, str] = {}
    function_index_by_module: dict[str, dict[str, list[str]]] = {}
    module_name_to_fqn: dict[str, list[str]] = {}
    ref_candidates_by_function: dict[str, list[dict[str, Any]]] = {}
    node_kinds_all: dict[str, str] = {}

    ast_total = len(source_ast_paths)
    ast_done = 0
    for file_result in extract_code_structure(source_ast_paths, data_dir=data_dir, run_dir=run_dir, lang=lang):
        ast_done += 1
        facts = file_result["facts"]
        cidx = file_result["cidx"]
        fields = file_result["fields"]

        # 누적
        function_facts_list.extend(facts)
        module_index_ast.update(cidx)
        variable_facts_list.extend(fields)
        for name, fqns in file_result["module_name_to_fqn"].items():
            module_name_to_fqn.setdefault(name, []).extend(fqns)
        for mid, types in file_result["sym_idx"].items():
            symbol_type_index.setdefault(mid, {}).update(types)
        function_index.update(file_result["meth_idx"])
        for cls, by_name in file_result["meth_by_cls"].items():
            for mname, ids in by_name.items():
                function_index_by_module.setdefault(cls, {}).setdefault(mname, []).extend(ids)
        for mid, cands in file_result["ref_cands"].items():
            ref_candidates_by_function.setdefault(mid, []).extend(cands)
        node_kinds_all.update(file_result.get("node_kinds", {}))

        # 파일별 Neo4j 스트리밍
        if facts or cidx:
            try:
                file_node_kinds = file_result.get("node_kinds", {})
                queries: list[dict[str, Any]] = []
                queries.extend(build_module_queries(cidx))
                queries.extend(build_function_queries(facts, node_kinds=file_node_kinds))
                queries.extend(build_variable_queries(fields, node_kinds=file_node_kinds, lang=lang))
                queries.extend(build_has_function_queries(facts, cidx))
                queries.extend(build_has_variable_queries(fields, cidx))
                if queries:
                    async for chunk in write_to_graph(client, queries, f"PHASE1:AST:{file_result['file_name']}"):
                        yield chunk
            except Exception as e:  # noqa: SILENT_FAIL — 한 파일 Neo4j 반영 실패가 다른 파일 진행을 막지 않도록 errlog 후 계속
                logger.error("[PHASE:1:AST] Neo4j 반영 실패 | file=%s error=%s", file_result["file_name"], e, exc_info=True)

        ast_progress = int((ast_done / ast_total) * 70) if ast_total else 70
        yield emit_phase_event(0, "AST 정적 그래프", "in_progress", ast_progress,
                               {"completed": ast_done, "total": ast_total, "file": file_result["file_name"]})

    # ── 패키지 노드 (전체 module_index 누적 후) ──
    package_fqns = _extract_package_fqns(module_index_ast)
    if package_fqns:
        try:
            expanded_pkg = _expand_package_fqns(package_fqns)
            pkg_queries: list[dict[str, Any]] = []
            pkg_queries.extend(build_package_queries(expanded_pkg))
            pkg_queries.extend(build_package_hierarchy_queries(package_fqns))
            if pkg_queries:
                async for chunk in write_to_graph(client, pkg_queries, "PHASE1:PACKAGE"):
                    yield chunk
        except Exception as e:  # noqa: SILENT_FAIL — 패키지 노드 반영 실패가 메서드/필드 처리를 막지 않도록 errlog 후 계속
            logger.error("[PHASE:1:PACKAGE] Neo4j 패키지 반영 실패 | error=%s", e, exc_info=True)

    # MODULE → PACKAGE 관계
    if module_index_ast:
        try:
            btp_queries = build_belongs_to_package_queries(module_index_ast)
            if btp_queries:
                async for chunk in write_to_graph(client, btp_queries, "PHASE1:BELONGS_TO_PACKAGE"):
                    yield chunk
        except Exception as e:  # noqa: SILENT_FAIL — MODULE→PACKAGE 관계 반영 실패가 다른 phase 1 진행을 막지 않도록 errlog 후 계속
            logger.error("[PHASE:1:PACKAGE] Neo4j MODULE→PACKAGE 관계 반영 실패 | error=%s", e, exc_info=True)

    # root PACKAGE 노드
    try:
        root_queries = build_root_package_queries(package_fqns, module_index_ast)
        if root_queries:
            async for chunk in write_to_graph(client, root_queries, "PHASE1:ROOT_PACKAGE"):
                yield chunk
    except Exception as e:  # noqa: SILENT_FAIL — root PACKAGE 반영 실패가 다른 phase 1 진행을 막지 않도록 errlog 후 계속
        logger.error("[PHASE:1:PACKAGE] Neo4j root 패키지 반영 실패 | error=%s", e, exc_info=True)

    stats.total_nodes += len(function_facts_list) + len(variable_facts_list)
    yield emit_message(f"  └─ AST 완료 | 메서드 {len(function_facts_list)}개, 클래스 {len(module_index_ast)}개, 필드 {len(variable_facts_list)}개")

    # ── Step 3: 아티팩트 파싱 ──
    sql_xml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.sql_jobs]
    entity_xml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.entity_jobs]
    config_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.config_jobs]

    yield emit_message(f"📄 아티팩트 파싱 | SQL {len(sql_xml_paths)}개, Entity {len(entity_xml_paths)}개, Config {len(config_paths)}개")
    sql_chunks, config_chunks, artifact_chunks = parse_config_and_artifacts(
        sql_xml_paths, config_paths, entity_jobs=entity_xml_paths,
    )

    if artifact_chunks:
        ct_counts = Counter(c.chunk_type for c in artifact_chunks)
        entity_count = sum(v for k, v in ct_counts.items() if k == "entity")
        breakdown = ", ".join(f"{k}={v}" for k, v in ct_counts.most_common())
        logger.info("[PHASE:1:ARTIFACT] artifact 전체=%d entity=%d | %s", len(artifact_chunks), entity_count, breakdown)

    # Neo4j: SqlStatement/Config/Artifact 노드
    if sql_chunks or config_chunks or artifact_chunks:
        try:
            queries = []
            queries.extend(build_sql_statement_queries(sql_chunks))
            queries.extend(build_config_section_queries(config_chunks))
            queries.extend(build_artifact_chunk_queries(artifact_chunks))
            if queries:
                async for chunk in write_to_graph(client, queries, "PHASE1:ARTIFACT"):
                    yield chunk
        except Exception as e:  # noqa: SILENT_FAIL — 아티팩트 노드 반영 실패가 인덱스 빌드를 막지 않도록 errlog 후 계속
            logger.error("[PHASE:1:ARTIFACT] Neo4j 아티팩트 반영 실패 | error=%s", e, exc_info=True)

    # ── Step 4: 인덱스 구축 ──
    sql_index, module_index, config_index, artifact_index = build_search_indexes(
        sql_chunks, module_index_ast, config_chunks, artifact_chunks=artifact_chunks,
    )

    # ctx.state 에 저장 (이미 ctx.state 참조 — alias 'st' 사용)
    st.data_dir = data_dir
    st.catalog = catalog
    st.plan = plan
    st.function_facts_list = function_facts_list
    st.module_index_ast = module_index_ast
    st.package_fqns = package_fqns
    st.variable_facts_list = variable_facts_list
    st.symbol_type_index = symbol_type_index
    st.function_index = function_index
    st.function_index_by_module = function_index_by_module
    st.module_name_to_fqn = module_name_to_fqn
    st.ref_candidates_by_function = ref_candidates_by_function
    st.sql_chunks = sql_chunks
    st.config_chunks = config_chunks
    st.artifact_chunks = artifact_chunks
    st.sql_index = sql_index
    st.module_index = module_index
    st.config_index = config_index
    st.artifact_index = artifact_index

    yield emit_message(f"✅ Phase 1 완료 | SQL {len(sql_chunks)}개, 설정 {len(config_chunks)}개, 아티팩트 {len(artifact_chunks)}개")
