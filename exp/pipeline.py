"""LangGraph 파이프라인: Scan → Java AST → Artifacts → Index → Linking → [DeepAgent] → Summary → CALLS → Output."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from typing_extensions import TypedDict

from exp.config import get_source_dir, get_nontarget_dir
from exp.chunk_bundle import build_bundle_span_index
from exp.steps import (
    scan_project,
    run_step2,
    run_step3,
    run_step4,
    derive_method_table_edges,
    run_step5,
    run_step6,
)
from exp.call_batch import derive_uses_from_calls
from exp.steps.step6_summary import (
    run_summary_enhancement_by_call_graph,
    run_field_summary,
    run_class_summary,
)

logger = logging.getLogger(__name__)

# 섹션 로그: 새 단계/하위작업 시작 시 구분용 (가독성)
def _section(title: str, detail: str = "") -> None:
    logger.info("")
    logger.info("-------- %s --------", title)
    if detail:
        logger.info("  %s", detail)


class State(TypedDict, total=False):
    """파이프라인 공유 상태. 노드는 갱신할 키만 반환."""
    data_dir: str
    args: Any
    run_dir: Optional[str]
    catalog: List[Any]
    plan: Any
    method_facts_list: List[Any]
    class_index_ast: Dict[str, Any]
    package_fqns: List[str]
    field_facts_list: List[Any]
    symbol_type_index: Dict[str, Dict[str, str]]
    method_index: Dict[str, str]
    method_index_by_class: Dict[str, Dict[str, List[str]]]
    class_index_simple: Dict[str, List[str]]
    ref_candidates_by_method: Dict[str, List[Dict[str, Any]]]
    sql_chunks: List[Any]
    config_chunks: List[Any]
    artifact_chunks: List[Any]
    sql_index: Dict[str, Any]
    class_index: Dict[str, Any]
    config_index: Dict[str, Any]
    artifact_index: Dict[str, Any]
    refer_to_edges: List[Any]
    reads_writes: List[Any]
    fk_edges: List[Any]
    column_refs: List[Any]
    method_table_edges: List[Any]
    unresolved: List[Any]
    span_index: Dict[str, Dict[str, Any]]
    chunk_index: Dict[str, Dict[str, Any]]
    summary_by_method: Dict[str, Dict[str, Any]]
    summary_by_field: Dict[str, Dict[str, Any]]
    summary_by_class: Dict[str, Dict[str, Any]]
    calls_edges_method: List[Any]
    calls_edges_from_batch: List[Any]
    uses_edges: List[Any]
    unresolved_calls: List[Any]
    user_stories: List[Dict[str, Any]]
    deepagent_new_refer_to_edges: List[Any]
    deepagent_new_method_table_edges: List[Any]


def _scan_node(state: State) -> State:
    data_dir = state["data_dir"]
    args = state["args"]
    source_dir = get_source_dir(data_dir)
    nontarget_dir = get_nontarget_dir(data_dir)
    _section("STEP1 스캔", "파일 목록 수집 및 분류 (java_ast, xml_sqlmap, yaml, ...) | source=%s" % source_dir)
    scan_roots = [
        (source_dir, "source"),
        (nontarget_dir, "analysis/nontarget"),
    ]
    catalog, plan = scan_project(scan_roots=scan_roots, use_pathlib=not getattr(args, "no_pathlib", False))
    logger.info(
        "[EXP:STEP1] 스캔 완료 | catalog=%d java_ast=%d xml_sqlmap=%d xml_ejb=%d xml_entitymodel=%d yaml=%d properties=%d json=%d docs=%d",
        len(catalog),
        len(plan.java_ast_jobs),
        len(plan.xml_sqlmap_jobs),
        len(plan.xml_ejb_jobs),
        len(plan.xml_entitymodel_jobs),
        len(plan.yaml_jobs),
        len(plan.properties_jobs),
        len(plan.json_jobs),
        len(plan.docs_jobs),
    )
    out = {"catalog": catalog, "plan": plan}
    run_dir = state.get("run_dir")
    if run_dir:
        from exp.artifact_dump import dump_step1_artifacts
        dump_step1_artifacts({**state, **out}, Path(run_dir) / "artifacts")
    return out


def _java_ast_node(state: State) -> State:
    data_dir = state["data_dir"]
    plan = state["plan"]
    base = Path(data_dir).resolve()
    java_ast_paths = [
        str(base / p) if not Path(p).is_absolute() else p
        for p in plan.java_ast_jobs
    ]
    _section("STEP2 Java AST", "AST 파싱 → MethodFacts, ChunkBundle 생성·저장, Neo4j 배치 | files=%d" % len(java_ast_paths))
    args = state.get("args")
    neo4j_writer = getattr(args, "neo4j_writer", None) if args else None
    neo4j_batch_size = getattr(args, "neo4j_batch_size", 30) if args else 30
    (
        method_facts_list,
        class_index_ast,
        package_fqns,
        field_facts_list,
        symbol_type_index,
        method_index,
        method_index_by_class,
        class_index_simple,
        ref_candidates_by_method,
    ) = run_step2(
        java_ast_paths,
        data_dir=data_dir,
        run_dir=state.get("run_dir"),
        neo4j_writer=neo4j_writer,
        neo4j_batch_size=neo4j_batch_size,
    )
    logger.info(
        "[EXP:STEP2] Java AST 완료 | MethodFacts=%d class_index=%d ref_candidates_methods=%d method_index=%d",
        len(method_facts_list),
        len(class_index_ast),
        len(ref_candidates_by_method),
        len(method_index),
    )
    out = {
        "method_facts_list": method_facts_list,
        "class_index_ast": class_index_ast,
        "package_fqns": package_fqns,
        "field_facts_list": field_facts_list,
        "symbol_type_index": symbol_type_index,
        "method_index": method_index,
        "method_index_by_class": method_index_by_class,
        "class_index_simple": class_index_simple,
        "ref_candidates_by_method": ref_candidates_by_method,
    }
    # Step2 직후 PACKAGE 노드 + 계층(BELONGS_TO_PACKAGE) 즉시 Neo4j 반영
    if neo4j_writer and neo4j_batch_size and package_fqns:
        try:
            from exp.neo4j import _expand_package_fqns, build_package_queries, build_package_hierarchy_queries
            expanded_pkg = _expand_package_fqns(package_fqns)
            q_pkg = build_package_queries(expanded_pkg)
            q_hierarchy = build_package_hierarchy_queries(package_fqns)
            asyncio.run(neo4j_writer.run_queries_batch_sync(q_pkg + q_hierarchy, batch_size=neo4j_batch_size))
            logger.info("[EXP:STEP2] Neo4j PACKAGE+계층 반영 | packages=%d hierarchy=%d", len(q_pkg), len(q_hierarchy))
        except Exception as e:
            logger.warning("[EXP:STEP2] Neo4j PACKAGE flush 스킵: %s", e)
    run_dir = state.get("run_dir")
    if run_dir:
        from exp.artifact_dump import dump_step2_artifacts
        dump_step2_artifacts({**state, **out}, Path(run_dir) / "artifacts")
    return out


def _artifacts_node(state: State) -> State:
    data_dir = state["data_dir"]
    plan = state["plan"]
    base = Path(data_dir).resolve()
    xml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.xml_sqlmap_jobs]
    yaml_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.yaml_jobs]
    prop_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.properties_jobs]
    xml_ejb_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.xml_ejb_jobs]
    xml_entitymodel_paths = [str(base / p) if not Path(p).is_absolute() else p for p in plan.xml_entitymodel_jobs]
    _section("STEP3 아티팩트", "MyBatis XML / YAML / properties / EJB·entitymodel 파싱 → SqlStatement·Config·Artifact 청크 | xml=%d yaml=%d" % (len(xml_paths), len(yaml_paths)))
    args = state.get("args")
    neo4j_writer = getattr(args, "neo4j_writer", None) if args else None
    neo4j_batch_size = getattr(args, "neo4j_batch_size", 30) if args else 30
    sql_chunks, config_chunks, artifact_chunks = run_step3(
        xml_paths, yaml_paths, prop_paths,
        xml_ejb_jobs=xml_ejb_paths,
        xml_entitymodel_jobs=xml_entitymodel_paths,
        neo4j_writer=neo4j_writer,
        neo4j_batch_size=neo4j_batch_size,
    )
    logger.info(
        "[EXP:STEP3] 아티팩트 완료 | SqlStatement=%d ConfigSection=%d ArtifactChunk=%d",
        len(sql_chunks), len(config_chunks), len(artifact_chunks),
    )
    out = {"sql_chunks": sql_chunks, "config_chunks": config_chunks, "artifact_chunks": artifact_chunks}
    run_dir = state.get("run_dir")
    if run_dir:
        from exp.artifact_dump import dump_step3_artifacts
        dump_step3_artifacts({**state, **out}, Path(run_dir) / "artifacts")
    return out


def _index_node(state: State) -> State:
    _section("STEP4 인덱스", "sql_index, class_index, config_index, artifact_index + span_index/chunk_index 빌드")
    sql_index, class_index, config_index, artifact_index = run_step4(
        state["sql_chunks"],
        state["method_facts_list"],
        state["class_index_ast"],
        state["config_chunks"],
        artifact_chunks=state["artifact_chunks"],
    )
    logger.info(
        "[EXP:STEP4] 인덱스 완료 | sql_index=%d class_index=%d config_index=%d artifact_index=%d",
        len(sql_index), len(class_index), len(config_index), len(artifact_index),
    )
    run_dir_raw = state.get("run_dir")
    chunk_bundle_dir = str(Path(run_dir_raw) / "chunk_bundles") if run_dir_raw else None
    span_index, chunk_index = build_bundle_span_index(
        data_dir=state["data_dir"],
        chunk_bundle_dir=chunk_bundle_dir,
    )
    out = {
        "sql_index": sql_index,
        "class_index": class_index,
        "config_index": config_index,
        "artifact_index": artifact_index,
        "span_index": span_index,
        "chunk_index": chunk_index,
    }
    if run_dir_raw:
        from exp.artifact_dump import dump_step4_artifacts
        dump_step4_artifacts({**state, **out}, Path(run_dir_raw) / "artifacts")
    return out


def _linking_node(state: State) -> State:
    _section("STEP5 링킹", "Method/Field→REFER_TO, SQL→테이블(LLM), Call batch, analysis LLM, CALLS/USES 해상도·병합·Neo4j")
    from exp.chunk_bundle import load_field_chunks_from_bundles
    from exp.config import get_chunk_bundle_dir_from_run_dir
    run_dir = state.get("run_dir")
    chunk_dir = get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    field_chunks = load_field_chunks_from_bundles(state["data_dir"], chunk_dir)
    writer = getattr(state.get("args"), "neo4j_writer", None)
    batch_size = getattr(state.get("args"), "neo4j_batch_size", 30) or 30
    refer_to_edges, reads_writes, fk_edges, column_refs, method_table_edges, unresolved, calls_edges_from_batch, unresolved_calls = run_step5(
        state["method_facts_list"],
        state["sql_index"],
        state["sql_chunks"],
        config_index=state["config_index"],
        ref_candidates_by_method=state.get("ref_candidates_by_method"),
        symbol_type_index=state.get("symbol_type_index"),
        class_index=state["class_index"],
        field_chunks=field_chunks,
        data_dir=state.get("data_dir"),
        field_facts_list=state.get("field_facts_list"),
        method_index_by_class=state.get("method_index_by_class"),
        run_dir=state.get("run_dir"),
        neo4j_writer=writer,
        neo4j_batch_size=batch_size,
        artifact_chunks=state.get("artifact_chunks"),
    )
    # CALLS: internal + external 모두 Neo4j 반영 (필터링은 프롬프트 단에서 처리). USES는 internal만.
    all_calls = list(calls_edges_from_batch or [])
    calls_edges_method = all_calls
    internal_calls = [e for e in all_calls if getattr(e, "scope", "internal") == "internal"]
    external_count = len(all_calls) - len(internal_calls)
    if external_count:
        logger.info(
            "[EXP:STEP5] CALLS: internal=%d external=%d (둘 다 Neo4j 반영)",
            len(internal_calls),
            external_count,
        )
    uses_edges = derive_uses_from_calls(calls_edges_method, state["method_facts_list"])
    logger.info(
        "[EXP:STEP5] CALLS/USES (call_batch 기반) | calls_edges=%d uses_edges=%d unresolved_calls=%d",
        len(calls_edges_method),
        len(uses_edges),
        len(unresolved_calls or []),
    )
    # Step5 Neo4j 반영은 run_step5 내부에서 3갈래(Table/Call/UML) 완료 시점별로 이미 flush됨. 여기서는 스킵.
    # Step5: analysis LLM (parallel) → summary_by_method. CALLS/USES는 이미 Neo4j 반영됨
    use_llm = not getattr(state.get("args"), "skip_llm", False)
    api_key = os.environ.get("LLM_API_KEY", "")
    logger.info("[EXP:STEP5] [섹션] analysis LLM  요약·메서드 단위 병렬")
    summary_by_method = run_step6(
        state["method_facts_list"],
        refer_to_edges,
        reads_writes,
        use_llm=use_llm,
        api_key=api_key,
        locale="ko",
        parallel=True,
        class_index=state.get("class_index"),
        sql_index=state.get("sql_index"),
        config_index=state.get("config_index"),
    )
    logger.info(
        "[EXP:STEP5] 링킹 완료  methods=%d  refer_to=%d  reads_writes=%d  calls=%d  uses=%d  unresolved=%d",
        len(state["method_facts_list"]),
        len(refer_to_edges),
        len(reads_writes),
        len(calls_edges_method),
        len(uses_edges),
        len(unresolved),
    )
    out = {
        "refer_to_edges": refer_to_edges,
        "reads_writes": reads_writes,
        "fk_edges": fk_edges,
        "column_refs": column_refs,
        "method_table_edges": method_table_edges,
        "unresolved": unresolved,
        "calls_edges_from_batch": calls_edges_from_batch,
        "unresolved_calls": unresolved_calls,
        "summary_by_method": summary_by_method,
        "calls_edges_method": calls_edges_method,
        "uses_edges": uses_edges,
    }
    run_dir = state.get("run_dir")
    if run_dir:
        from exp.artifact_dump import dump_step5_artifacts
        dump_step5_artifacts({**state, **out}, Path(run_dir) / "artifacts")
    return out


def _route_after_linking(state: State) -> Literal["deepagent", "summary"]:
    unresolved = state.get("unresolved")
    skip = getattr(state.get("args"), "skip_deepagent", False)
    if unresolved and not skip:
        logger.info(
            "[EXP:ROUTE] [EXP:DEEPAGENT] 다음: deepagent 노드 실행 | unresolved=%d건",
            len(unresolved),
        )
        return "deepagent"
    n = len(unresolved) if unresolved else 0
    logger.info(
        "[EXP:ROUTE] [EXP:DEEPAGENT] DeepAgent 스킵 → summary 직행 | unresolved=%s skip_deepagent=%s",
        n,
        skip,
    )
    return "summary"


def _deepagent_node(state: State) -> State:
    from exp.agent import run_deep_agent
    import os
    unresolved = state["unresolved"]
    _section("DEEPAGENT (unresolved 해결)", "LLM으로 미연결 참조 해결 | unresolved=%d" % len(unresolved))
    logger.info("[EXP:DEEPAGENT] 노드 진입 | run_deep_agent 호출 (unresolved=%d)", len(unresolved))
    api_key = os.getenv("LLM_API_KEY", "")
    run_dir_raw = state.get("run_dir")
    run_dir = Path(run_dir_raw) if run_dir_raw else None
    new_refer, new_mt = run_deep_agent(unresolved, state, api_key or None, run_dir=run_dir)
    logger.info("[EXP:DEEPAGENT] 노드 완료 | new_refer=%d new_mt=%d", len(new_refer), len(new_mt))
    refer_to_edges = list(state["refer_to_edges"]) + list(new_refer)
    method_table_edges = derive_method_table_edges(refer_to_edges, state["reads_writes"])
    logger.info(
        "[EXP:DEEPAGENT] 완료 | new_refer=%d new_mt=%d refer_to_total=%d",
        len(new_refer),
        len(new_mt),
        len(refer_to_edges),
    )
    # DeepAgent 산출물 즉시 Neo4j 반영 (새 REFER_TO/CALLS_SQL, Method→Table)
    writer = getattr(state.get("args"), "neo4j_writer", None)
    batch_size = getattr(state.get("args"), "neo4j_batch_size", 30) or 30
    if writer and batch_size and (new_refer or new_mt):
        try:
            from exp.neo4j import build_refer_to_queries, build_method_table_from_writes_queries
            q_ref = build_refer_to_queries(new_refer)
            q_mt = build_method_table_from_writes_queries(new_mt)
            asyncio.run(writer.run_queries_batch_sync(q_ref + q_mt, batch_size=batch_size))
            logger.info("[EXP:DEEPAGENT] Neo4j 반영 | new_refer=%d new_mt=%d", len(q_ref), len(q_mt))
        except Exception as e:
            logger.warning("[EXP:DEEPAGENT] Neo4j flush 스킵: %s", e)
    out = {
        "refer_to_edges": refer_to_edges,
        "method_table_edges": method_table_edges,
        "unresolved": [],
        "deepagent_new_refer_to_edges": new_refer,
        "deepagent_new_method_table_edges": new_mt,
    }
    run_dir = state.get("run_dir")
    if run_dir:
        from exp.artifact_dump import dump_deepagent_artifacts
        dump_deepagent_artifacts({**state, **out}, Path(run_dir) / "artifacts")
    return out


def _summary_node(state: State) -> State:
    args = state["args"]
    _section("STEP6 역할/요약", "호출 그래프 기반 보강, 스테레오타입, method summary만 Neo4j 반영")
    summary_by_method = state.get("summary_by_method") or {}
    calls_edges_method: List[Any] = []
    writer = getattr(args, "neo4j_writer", None)
    if writer and hasattr(writer, "fetch_calls_edges"):
        try:
            rows = asyncio.run(writer.fetch_calls_edges())
            if rows:
                from exp.models import CallsEdge
                calls_edges_method = [
                    CallsEdge(
                        caller_method_id=str(r.get("caller_method_id") or ""),
                        callee_method_id=str(r.get("callee_method_id") or ""),
                        call_code="",
                    )
                    for r in rows
                ]
                logger.info("[EXP:STEP6] Neo4j에서 CALLS 조회  edges=%d", len(calls_edges_method))
        except Exception as e:
            logger.warning("[EXP:STEP6] Neo4j CALLS 조회 실패, state 사용: %s", e)
    if not calls_edges_method:
        calls_edges_method = list(state.get("calls_edges_method") or [])
    unresolved_calls = list(state.get("unresolved_calls") or [])
    if not summary_by_method:
        logger.info("[EXP:STEP6] summary_by_method 없음  호출 그래프 보강 스킵")
    else:
        logger.info("[EXP:STEP6] Step5 결과 사용  summary_by_method=%d  calls_edges=%d", len(summary_by_method), len(calls_edges_method))
        logger.info("[EXP:STEP6] [섹션] 호출 그래프 기반 요약 보강 (동일 레벨 병렬)")
        api_key = os.environ.get("LLM_API_KEY", "")
        summary_by_method = run_summary_enhancement_by_call_graph(
            summary_by_method,
            calls_edges_method,
            state["method_facts_list"],
            api_key=api_key,
            locale="ko",
            class_index=state.get("class_index"),
        )
    api_key = os.environ.get("LLM_API_KEY", "")
    batch_size = getattr(args, "neo4j_batch_size", 30) or 30
    if writer and batch_size:
        try:
            from exp.neo4j import build_method_summary_queries
            q_summary = build_method_summary_queries(summary_by_method)
            if q_summary:
                asyncio.run(writer.run_queries_batch_sync(q_summary, batch_size=batch_size))
                cmd = sum(1 for _, d in summary_by_method.items() if (d.get("stereotype") or "").strip().lower() == "command")
                rdm = sum(1 for _, d in summary_by_method.items() if (d.get("stereotype") or "").strip().lower() == "readmodel")
                logger.info("[EXP:STEP6] Neo4j 배치 flush  method summary·stereotype  queries=%d  command=%d  readmodel=%d", len(q_summary), cmd, rdm)
            # 의존/합성/집합(DEPENDENCY, COMPOSITION, AGGREGATION) — summary_by_method.dependencies 기반
            from exp.neo4j import build_uml_dependency_queries
            q_dep = build_uml_dependency_queries(
                summary_by_method,
                state.get("method_facts_list") or [],
                state.get("class_index") or {},
            )
            if q_dep:
                asyncio.run(writer.run_queries_batch_sync(q_dep, batch_size=batch_size))
                logger.info("[EXP:STEP6] Neo4j 배치 flush  UML DEPENDENCY/COMPOSITION/AGGREGATION  queries=%d", len(q_dep))
        except Exception as e:
            logger.warning("[EXP:STEP6] Neo4j 배치 flush 스킵: %s", e)
    # Field 요약 (병렬만, 호출 그래프 없음)
    logger.info("[EXP:STEP6] [섹션] Field 요약")
    field_facts_list = state.get("field_facts_list") or []
    summary_by_field = run_field_summary(
        field_facts_list,
        state.get("class_index") or {},
        data_dir=state.get("data_dir", ""),
        run_dir=state.get("run_dir"),
        api_key=os.environ.get("LLM_API_KEY", ""),
        locale="ko",
    )
    if writer and batch_size and summary_by_field:
        try:
            from exp.neo4j import build_field_summary_queries
            q_f = build_field_summary_queries(summary_by_field)
            if q_f:
                asyncio.run(writer.run_queries_batch_sync(q_f, batch_size=batch_size))
                logger.info("[EXP:STEP6] Neo4j 배치 flush  field summary  queries=%d", len(q_f))
        except Exception as e:
            logger.warning("[EXP:STEP6] Neo4j field summary flush 스킵: %s", e)
    # Class 요약 (청킹 + prev_summary)
    logger.info("[EXP:STEP6] [섹션] Class 요약")
    summary_by_class = run_class_summary(
        state.get("class_index") or {},
        summary_by_method,
        summary_by_field,
        state.get("method_facts_list") or [],
        field_facts_list,
        api_key=os.environ.get("LLM_API_KEY", ""),
        locale="ko",
    )
    if writer and batch_size and summary_by_class:
        try:
            from exp.neo4j import build_class_summary_queries
            q_c = build_class_summary_queries(summary_by_class)
            if q_c:
                asyncio.run(writer.run_queries_batch_sync(q_c, batch_size=batch_size))
                logger.info("[EXP:STEP6] Neo4j 배치 flush  class summary  queries=%d", len(q_c))
        except Exception as e:
            logger.warning("[EXP:STEP6] Neo4j class summary flush 스킵: %s", e)
    user_stories: List[Dict[str, Any]] = []
    try:
        from exp.steps.step6_summary import run_user_story_generation
        summary_by_class_for_us = summary_by_class or {}
        user_stories = run_user_story_generation(
            summary_by_class_for_us,
            state.get("class_index") or {},
            api_key=api_key,
            locale="ko",
        )
        if not user_stories:
            with_summary = sum(1 for d in summary_by_class_for_us.values() if (d or {}).get("summary"))
            logger.info("[EXP:STEP6] User story 0개  summary_by_class=%d  summary있음=%d  api_key=%s", len(summary_by_class_for_us), with_summary, bool(api_key))
        elif writer and batch_size:
            try:
                from exp.neo4j import build_user_story_queries
                q_us = build_user_story_queries(user_stories)
                if q_us:
                    asyncio.run(writer.run_queries_batch_sync(q_us, batch_size=batch_size))
                    logger.info("[EXP:STEP6] Neo4j 배치 flush  USER_STORY·HAS_USER_STORY·ACCEPTANCE_CRITERIA·HAS_AC  queries=%d", len(q_us))
            except Exception as ex:
                logger.warning("[EXP:STEP6] Neo4j user story flush 스킵: %s", ex)
    except Exception as e:
        logger.warning("[EXP:STEP6] User story 생성 스킵: %s", e)
    out = {
        "summary_by_method": summary_by_method,
        "summary_by_field": summary_by_field,
        "summary_by_class": summary_by_class,
        "unresolved_calls": unresolved_calls,
        "user_stories": user_stories,
    }
    run_dir = state.get("run_dir")
    if run_dir:
        from exp.artifact_dump import dump_step6_artifacts
        dump_step6_artifacts({**state, **out}, Path(run_dir) / "artifacts")
    return out


def _write_output_node(state: State) -> State:
    """출력은 run()에서 state 기반으로 수행. 노드는 통과."""
    return {}


def _build_graph():
    from langgraph.graph import StateGraph, START, END
    builder = StateGraph(State)
    builder.add_node("scan", _scan_node)
    builder.add_node("java_ast", _java_ast_node)
    builder.add_node("artifacts", _artifacts_node)
    builder.add_node("index", _index_node)
    builder.add_node("linking", _linking_node)
    builder.add_node("deepagent", _deepagent_node)
    builder.add_node("summary", _summary_node)
    builder.add_node("write_output", _write_output_node)

    builder.add_edge(START, "scan")
    builder.add_edge("scan", "java_ast")
    builder.add_edge("java_ast", "artifacts")
    builder.add_edge("artifacts", "index")
    builder.add_edge("index", "linking")
    builder.add_conditional_edges(
        "linking",
        _route_after_linking,
        {"deepagent": "deepagent", "summary": "summary"},
    )
    builder.add_edge("deepagent", "summary")
    builder.add_edge("summary", "write_output")
    builder.add_edge("write_output", END)
    return builder.compile()


_graph: Optional[Any] = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def run(data_dir: str, args: Any) -> State:
    """파이프라인 실행. 초기 state로 invoke 후 최종 state 반환."""
    initial: State = {
        "data_dir": data_dir,
        "args": args,
        "run_dir": str(args.run_dir) if getattr(args, "run_dir", None) else None,
    }
    graph = get_graph()
    return graph.invoke(initial)
