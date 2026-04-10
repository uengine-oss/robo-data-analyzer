"""Phase 2: 참조 해석 (LLM 링킹).

3단계로 구성:
1. 결정론적 매칭 + LLM 배치 (Table/Call 병렬)
2. DeepAgent 3종 병렬 (엔티티 식별 + 변수 참조 + 미해결 추적)
3. 파생 엣지 + FK 반영
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from util.ndjson_stream import emit_message, emit_data, PhaseTracker
from analyzer.strategy.framework.parsing.chunk_bundle import load_field_chunks_from_bundles
from analyzer.strategy.framework.linking.orchestrator import resolve_references
from analyzer.strategy.framework.linking.call_linker import derive_uses_from_calls
from analyzer.strategy.framework.common.models import (
    CallsEdge, ReferToEdge, ReadsWritesEdge, FkEdge, ColumnRef,
    FunctionTableEdge, ModuleTableEdge, ArtifactTableEdge, UnresolvedLink,
    UsesEdge,
)
from analyzer.strategy.framework.graph.queries import (
    build_refer_to_queries,
    build_reads_writes_queries,
    build_calls_queries,
    build_uses_queries,
    build_table_queries_from_reads_writes,
    build_column_queries,
    build_has_column_queries,
    build_node_table_queries,
    build_table_from_entity_classes,
    build_module_table_refer_to_queries,
    build_artifact_table_refer_to_queries,
    build_artifact_module_refer_to_queries,
    build_fk_to_table_queries,
    build_fk_to_column_queries,
)
from analyzer.common.graph_writer import run_graph_queries
from analyzer.strategy.framework.pipeline.progress import write_to_graph
from analyzer.strategy.framework.linking.agent import (
    run_deep_agent,
    run_deep_agent_entity_identification,
    run_variable_references,
)
from analyzer.strategy.framework.linking.deterministic import has_external_entity_info

logger = logging.getLogger(__name__)


# ── 헬퍼: entity FQN 수집 ──

def _collect_entity_fqns_from_edges(
    edges: list,
    module_index: Dict[str, Any],
) -> Dict[str, str]:
    """DeepAgent FunctionTableEdge에서 table_name을 추출하고,
    module_index에서 동일한 이름의 클래스/구조체를 매칭."""
    table_names_normalized = {
        e.table_name.upper().replace("_", ""): e.table_name
        for e in edges if e.table_name
    }
    entity_map: Dict[str, str] = {}
    for fqn, info in module_index.items():
        if not isinstance(info, dict):
            continue
        module_name = info.get("name", "")
        normalized = module_name.upper().replace("_", "") if module_name else ""
        if normalized in table_names_normalized:
            entity_map[fqn] = table_names_normalized[normalized]
    return entity_map


# ═══════════════════════════════════════════════════════════════════════
# 메인 Phase 함수
# ═══════════════════════════════════════════════════════════════════════

async def run_phase_linking(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2: 참조 해석 → LLM 배치 → DeepAgent → FK 반영."""
    st = pipeline._pipeline_state

    yield emit_message("🔗 링킹 시작 (참조 해석 → SQL→Table → Call → DeepAgent)")

    tracker = PhaseTracker(1, "LLM 링킹", total_phases=3)
    tracker.define_steps([
        ("결정론적 링킹", 10),
        ("LLM 배치 링킹", 55),
        ("후처리", 10),
        ("DeepAgent 의미 추적", 25),
    ])

    # Step 1: 결정론적 매칭 + LLM 배치
    ctx = _LinkingContext(st, tracker, client)
    async for chunk in _run_deterministic_and_llm_batch(ctx):
        yield chunk

    # Step 2: DeepAgent 3종 병렬
    async for chunk in _run_deep_agent_parallel(ctx):
        yield chunk

    # Step 3: FK 반영 + 상태 저장
    async for chunk in _run_post_linking(ctx, stats):
        yield chunk

    yield emit_message(
        f"✅ Phase 2 완료 | 참조 {len(ctx.refer_to_edges)}건, "
        f"CALLS {len(ctx.all_calls)}건, "
        f"Table배치 {ctx.table_batch_count}건, Call배치 {ctx.call_batch_count}건"
    )


# ═══════════════════════════════════════════════════════════════════════
# 내부 컨텍스트 (Phase 2 내에서만 사용)
# ═══════════════════════════════════════════════════════════════════════

class _LinkingContext:
    """Phase 2 내부 상태. Phase 함수 간 데이터 공유용."""
    __slots__ = (
        "st", "tracker", "client", "data_dir", "api_key",
        "refer_to_edges", "reads_writes", "fk_edges", "column_refs",
        "function_table_edges", "module_table_edges", "artifact_table_edges",
        "unresolved", "all_calls", "calls_edges_from_batch",
        "unresolved_calls", "uses_edges", "table_descriptions",
        "table_batch_count", "call_batch_count",
    )

    def __init__(self, st, tracker, client):
        self.st = st
        self.tracker = tracker
        self.client = client
        self.data_dir = st.data_dir or settings.path.data_dir
        self.api_key = settings.llm.api_key or os.environ.get("LLM_API_KEY", "")

        # 누적 결과
        self.refer_to_edges: list = []
        self.reads_writes: list = []
        self.fk_edges: list = []
        self.column_refs: list = []
        self.function_table_edges: list = []
        self.module_table_edges: list = []
        self.artifact_table_edges: list = []
        self.unresolved: list = []
        self.all_calls: list = []
        self.calls_edges_from_batch: list = []
        self.unresolved_calls: list = []
        self.uses_edges: list = []
        self.table_descriptions: dict = {}
        self.table_batch_count = 0
        self.call_batch_count = 0


# ═══════════════════════════════════════════════════════════════════════
# Step 1: 결정론적 매칭 + LLM 배치
# ═══════════════════════════════════════════════════════════════════════

async def _run_deterministic_and_llm_batch(ctx: _LinkingContext) -> AsyncGenerator[bytes, None]:
    """결정론적 매칭 → Table/Call LLM 배치 병렬 → 후처리 엣지 유도."""
    st = ctx.st
    field_chunks = load_field_chunks_from_bundles(ctx.data_dir)
    sql_index = st.sql_index
    sql_chunks = st.sql_chunks

    async for step_result in resolve_references(
        st.function_facts_list,
        sql_index,
        sql_chunks,
        config_index=st.config_index,
        ref_candidates_by_function=st.ref_candidates_by_function,
        symbol_type_index=st.symbol_type_index,
        module_index=st.module_index,
        field_chunks=field_chunks,
        data_dir=ctx.data_dir,
        variable_facts_list=st.variable_facts_list,
        function_index_by_module=st.function_index_by_module,
        run_dir=st.run_dir,
        artifact_index=st.artifact_index,
        artifact_chunks=st.artifact_chunks,
        ddl_table_metadata=st.ddl_table_metadata,
        lang=st.lang,
    ):
        stage = step_result["stage"]

        if stage == "deterministic":
            ctx.refer_to_edges = step_result["refer_to_edges"]
            ctx.unresolved = step_result["unresolved"]
            try:
                queries = build_refer_to_queries(ctx.refer_to_edges)
                if queries:
                    async for chunk in write_to_graph(ctx.client, queries, "PHASE2:DETERMINISTIC"):
                        yield chunk
            except Exception as e:
                logger.error("[PHASE:2:DETERMINISTIC] Neo4j 반영 실패 | error=%s", e, exc_info=True)
                raise
            yield ctx.tracker.begin_step(0, 1)
            yield ctx.tracker.end_step(f"참조 {len(ctx.refer_to_edges)}건, 미해결 {len(ctx.unresolved)}건")
            est_batches = max(1, (len(sql_chunks) + 19) // 20 + (len(st.function_facts_list) + 19) // 20)
            yield ctx.tracker.begin_step(1, est_batches)

        elif stage == "table_batch":
            # 데이터 수집
            ctx.reads_writes.extend(step_result["edges"])
            ctx.fk_edges.extend(step_result["fk"])
            ctx.column_refs.extend(step_result["col"])
            ctx.table_descriptions.update(step_result["desc"] or {})
            ctx.table_batch_count += 1
            # Neo4j 반영
            try:
                queries: list = []
                queries.extend(build_reads_writes_queries(step_result["edges"]))
                queries.extend(build_table_queries_from_reads_writes(step_result["edges"], table_descriptions=step_result["desc"] or {}))
                if step_result["col"]:
                    queries.extend(build_column_queries(step_result["col"]))
                    queries.extend(build_has_column_queries(step_result["col"]))
                if queries:
                    async for chunk in write_to_graph(ctx.client, queries, "PHASE2:TABLE_BATCH"):
                        yield chunk
            except Exception as e:
                logger.error("[PHASE:2:TABLE_LLM] Neo4j 반영 실패 | error=%s", e, exc_info=True)
            yield ctx.tracker.tick_quiet()

        elif stage == "call_batch":
            # 데이터 수집
            batch_call_edges = step_result["edges"]
            batch_call_unresolved = step_result["unresolved"]
            ctx.all_calls.extend(batch_call_edges)
            ctx.calls_edges_from_batch.extend(batch_call_edges)
            ctx.unresolved_calls.extend(batch_call_unresolved)
            uses_batch = derive_uses_from_calls(batch_call_edges, ctx.st.function_facts_list)
            ctx.uses_edges.extend(uses_batch)
            ctx.call_batch_count += 1
            # Neo4j 반영
            try:
                queries = []
                queries.extend(build_calls_queries(batch_call_edges))
                queries.extend(build_uses_queries(uses_batch))
                if queries:
                    async for chunk in write_to_graph(ctx.client, queries, "PHASE2:CALL_BATCH"):
                        yield chunk
            except Exception as e:
                logger.error("[PHASE:2:CALL_LLM] Neo4j 반영 실패 | error=%s", e, exc_info=True)
            yield ctx.tracker.tick_quiet()

        elif stage == "post":
            yield ctx.tracker.end_step(f"Table LLM {ctx.table_batch_count}건, Call LLM {ctx.call_batch_count}건")
            ctx.reads_writes = step_result["reads_writes"]
            ctx.fk_edges = step_result["fk_edges"]
            ctx.column_refs = step_result["column_refs"]
            ctx.function_table_edges = step_result["function_table_edges"]
            ctx.module_table_edges = step_result["module_table_edges"]
            ctx.artifact_table_edges = step_result["artifact_table_edges"]
            # 후처리 Neo4j 반영
            try:
                queries = []
                queries.extend(build_node_table_queries(ctx.function_table_edges))
                queries.extend(build_module_table_refer_to_queries(ctx.module_table_edges))
                queries.extend(build_artifact_table_refer_to_queries(ctx.artifact_table_edges))
                artifact_chunks = st.artifact_chunks or []
                module_index = st.module_index or {}
                queries.extend(build_artifact_module_refer_to_queries(artifact_chunks, module_index))
                if queries:
                    async for chunk in write_to_graph(ctx.client, queries, "PHASE2:POST"):
                        yield chunk
            except Exception as e:
                logger.error("[PHASE:2:POST] Neo4j 후처리 반영 실패 | error=%s", e, exc_info=True)
            yield ctx.tracker.begin_step(2, 1)
            yield ctx.tracker.end_step(
                f"Function→Table {len(ctx.function_table_edges)}건, "
                f"Module→Table {len(ctx.module_table_edges)}건, "
                f"Artifact→Table {len(ctx.artifact_table_edges)}건"
            )


# ═══════════════════════════════════════════════════════════════════════
# Step 2: DeepAgent 3종 병렬
# ═══════════════════════════════════════════════════════════════════════

async def _run_deep_agent_parallel(ctx: _LinkingContext) -> AsyncGenerator[bytes, None]:
    """엔티티 식별 + 변수 참조 + 미해결 참조 추적을 병렬 실행."""
    st = ctx.st
    ddl_meta = st.ddl_table_metadata or {}
    _has_external = has_external_entity_info(
        artifact_index=st.artifact_index,
        module_index=st.module_index,
    )
    _needs_deep_entity = not _has_external  # 단순화: 외부 엔티티 정보 없으면 DeepAgent 필요

    _SENTINEL = object()
    result_queue: asyncio.Queue = asyncio.Queue()

    # ── 병렬 작업 정의 ──

    def _run_entity_identification():
        if not _needs_deep_entity:
            from analyzer.strategy.framework.linking.deterministic import identify_entity_classes
            auto_fqns = identify_entity_classes(st.module_index or {})
            if auto_fqns:
                result_queue.put_nowait(("auto_entity", auto_fqns))
            return

        scenario = "ddl_only" if ddl_meta else "no_external"
        logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 시작 | scenario=%s", scenario)
        entity_state = {
            "sql_index": st.sql_index,
            "config_index": st.config_index or {},
            "artifact_index": st.artifact_index or {},
            "data_dir": ctx.data_dir,
            "run_dir": str(Path(st.run_dir)) if st.run_dir else None,
            "span_index": st.span_index,
            "chunk_index": st.chunk_index,
            "ddl_table_metadata": ddl_meta or None,
        }
        mt_edges, fk_e = run_deep_agent_entity_identification(
            st.module_index or {}, entity_state,
            api_key=settings.llm.api_key,
            run_dir=Path(st.run_dir) if st.run_dir else None,
            ddl_table_metadata=ddl_meta or None,
        )
        logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 완료 | tables=%d fk=%d", len(mt_edges), len(fk_e))
        result_queue.put_nowait(("entity", entity_state, mt_edges, fk_e))

    def _run_variable_refs():
        """변수 참조 + INIT_BY를 결정론적으로 매칭한다 (LLM 미사용)."""
        _vv = st.variable_facts_list or []
        if not _vv:
            return
        logger.info("[PHASE:2:VARREF] 결정론적 매칭 시작 | variables=%d", len(_vv))
        varref_state = {
            "sql_index": st.sql_index,
            "config_index": st.config_index or {},
            "artifact_index": st.artifact_index or {},
            "data_dir": ctx.data_dir,
            "run_dir": str(Path(st.run_dir)) if st.run_dir else None,
            "span_index": st.span_index,
            "chunk_index": st.chunk_index,
        }
        # REFERENCES 매칭
        refs = run_variable_references(_vv, state=varref_state)
        logger.info("[PHASE:2:VARREF] REFERENCES 매칭 완료 | refs=%d", len(refs))
        if refs:
            result_queue.put_nowait(("var_refs", refs))
        # INIT_BY 매칭
        from analyzer.strategy.framework.linking.init_by import build_init_by_edges
        init_by_edges = build_init_by_edges(_vv)
        if init_by_edges:
            result_queue.put_nowait(("init_by", init_by_edges))

    def _run_unresolved_tracking():
        if not ctx.unresolved:
            return
        logger.info("[PHASE:2:DEEP_AGENT:UNRESOLVED] 시작 | items=%d", len(ctx.unresolved))
        run_dir_path = Path(st.run_dir) if st.run_dir else None
        deep_state = {
            "sql_index": st.sql_index,
            "config_index": st.config_index or {},
            "artifact_index": st.artifact_index or {},
            "data_dir": ctx.data_dir,
            "run_dir": str(run_dir_path) if run_dir_path else None,
            "span_index": st.span_index,
            "chunk_index": st.chunk_index,
            "ddl_table_metadata": ddl_meta or None,
        }
        for item_refer, item_mt, item_fk in run_deep_agent(
            ctx.unresolved, deep_state, api_key=settings.llm.api_key, run_dir=run_dir_path,
        ):
            result_queue.put_nowait(("da_item", item_refer, item_mt, item_fk))
        result_queue.put_nowait(("da_state", deep_state))
        logger.info("[PHASE:2:DEEP_AGENT:UNRESOLVED] 완료")

    # ── 병렬 실행 + Queue 소비 ──

    async def _run_all():
        await asyncio.gather(
            asyncio.to_thread(_run_entity_identification),
            asyncio.to_thread(_run_variable_refs),
            asyncio.to_thread(_run_unresolved_tracking),
        )
        await result_queue.put(_SENTINEL)

    parallel_task = asyncio.ensure_future(_run_all())

    yield ctx.tracker.begin_step(3, max(1, len(ctx.unresolved)))
    da_refer_all: list = []
    da_mt_all: list = []
    da_fk_all: list = []

    while True:
        item = await result_queue.get()
        if item is _SENTINEL:
            break

        kind = item[0]

        if kind == "entity":
            async for chunk in _handle_entity_result(ctx, item, ddl_meta):
                yield chunk

        elif kind == "auto_entity":
            async for chunk in _handle_auto_entity_result(ctx, item, ddl_meta):
                yield chunk

        elif kind == "var_refs":
            async for chunk in _handle_varref_result(ctx, item):
                yield chunk

        elif kind == "init_by":
            async for chunk in _handle_init_by_result(ctx, item):
                yield chunk

        elif kind == "da_item":
            _, item_refer, item_mt, item_fk = item
            da_refer_all.extend(item_refer)
            da_mt_all.extend(item_mt)
            da_fk_all.extend(item_fk)
            async for chunk in _handle_da_item_result(ctx, item_refer, item_mt):
                yield chunk

        elif kind == "da_state":
            deep_state = item[1]
            if deep_state.get("span_index") and st.span_index is None:
                st.span_index = deep_state["span_index"]
            if deep_state.get("chunk_index") and st.chunk_index is None:
                st.chunk_index = deep_state["chunk_index"]

    await parallel_task

    # 미해결 추적 후처리
    if da_refer_all:
        ctx.refer_to_edges.extend(da_refer_all)
    if da_mt_all:
        ctx.function_table_edges.extend(da_mt_all)
    if da_fk_all:
        ctx.fk_edges.extend(da_fk_all)
    if da_refer_all and ctx.artifact_table_edges:
        from analyzer.strategy.framework.linking.orchestrator import derive_function_table_edges
        da_derived = derive_function_table_edges(da_refer_all, ctx.reads_writes, artifact_table_edges=ctx.artifact_table_edges)
        if da_derived:
            ctx.function_table_edges.extend(da_derived)
            try:
                dq = build_node_table_queries(da_derived)
                if dq:
                    async for chunk in write_to_graph(ctx.client, dq, "PHASE2:DEEPAGENT_DERIVED"):
                        yield chunk
            except Exception as e:
                logger.error("[PHASE:2:DEEP_AGENT] Neo4j 유도 반영 실패 | error=%s", e, exc_info=True)

    yield ctx.tracker.end_step(f"DeepAgent 완료 | REFER_TO {len(da_refer_all)}건")


# ── DeepAgent 결과 핸들러 (각각 독립 함수) ──

async def _handle_entity_result(ctx, item, ddl_meta):
    _, entity_state, entity_mt_edges, entity_fk_edges = item
    if entity_fk_edges:
        ctx.fk_edges.extend(entity_fk_edges)
    if entity_mt_edges:
        ctx.function_table_edges.extend(entity_mt_edges)
        try:
            if not ddl_meta:
                _entity_map = _collect_entity_fqns_from_edges(entity_mt_edges, ctx.st.module_index or {})
                if _entity_map:
                    eq = build_table_from_entity_classes(_entity_map, ctx.st.module_index or {}, variable_facts_list=ctx.st.variable_facts_list or [])
                    if eq:
                        async for chunk in write_to_graph(ctx.client, eq, "PHASE2:ENTITY_TABLE"):
                            yield chunk
            eq2 = build_node_table_queries(entity_mt_edges)
            if eq2:
                async for chunk in write_to_graph(ctx.client, eq2, "PHASE2:ENTITY_ID"):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:2:DEEP_AGENT:ENTITY] Neo4j 반영 실패 | error=%s", e, exc_info=True)
    if entity_state and entity_state.get("span_index") and ctx.st.span_index is None:
        ctx.st.span_index = entity_state["span_index"]
    if entity_state and entity_state.get("chunk_index") and ctx.st.chunk_index is None:
        ctx.st.chunk_index = entity_state["chunk_index"]


async def _handle_auto_entity_result(ctx, item, ddl_meta):
    auto_entity_fqns = item[1]
    logger.info("[PHASE:2:DEEP_AGENT:ENTITY] 자동 식별 (어노테이션) | classes=%d", len(auto_entity_fqns))
    if not ddl_meta:
        try:
            _mi = ctx.st.module_index or {}
            auto_map = {fqn: _mi[fqn].get("name", "") for fqn in auto_entity_fqns if fqn in _mi and isinstance(_mi[fqn], dict)}
            aq = build_table_from_entity_classes(auto_map, _mi, variable_facts_list=ctx.st.variable_facts_list or [])
            if aq:
                async for chunk in write_to_graph(ctx.client, aq, "PHASE2:AUTO_ENTITY_TABLE"):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:2:DEEP_AGENT:ENTITY] 자동 엔티티 Table 생성 실패 | error=%s", e, exc_info=True)


async def _handle_varref_result(ctx, item):
    refs = item[1]
    try:
        if refs:
            vq = [{
                "query": (
                    "UNWIND $rows AS row "
                    "MATCH (f:FUNCTION {function_id: row.fid}) "
                    "MATCH (v {variable_id: row.vid}) WHERE v:VARIABLE OR v:CONSTANT "
                    "MERGE (f)-[:REFERENCES]->(v)"
                ),
                "params": {"rows": [{"fid": fid, "vid": vid} for fid, vid in refs]},
            }]
            async for chunk in write_to_graph(ctx.client, vq, "PHASE2:VAR_REFS"):
                yield chunk
        logger.info("[PHASE:2:DEEP_AGENT:VARREF] 변수 참조 생성 | refs=%d", len(refs))
    except Exception as e:
        logger.error("[PHASE:2:DEEP_AGENT:VARREF] Neo4j 반영 실패 | error=%s", e, exc_info=True)


async def _handle_init_by_result(ctx, item):
    """INIT_BY 관계를 Neo4j에 반영한다."""
    init_by_edges = item[1]
    try:
        from analyzer.strategy.framework.graph.queries import build_init_by_queries
        iq = build_init_by_queries(init_by_edges)
        if iq:
            async for chunk in write_to_graph(ctx.client, iq, "PHASE2:INIT_BY"):
                yield chunk
        logger.info("[PHASE:2:INIT_BY] 초기화 관계 생성 | edges=%d", len(init_by_edges))
    except Exception as e:
        logger.error("[PHASE:2:INIT_BY] Neo4j 반영 실패 | error=%s", e, exc_info=True)


async def _handle_da_item_result(ctx, item_refer, item_mt):
    try:
        dq: list = []
        if item_refer:
            dq.extend(build_refer_to_queries(item_refer))
        if item_mt:
            dq.extend(build_node_table_queries(item_mt))
        if dq:
            async for chunk in write_to_graph(ctx.client, dq, "PHASE2:DEEPAGENT"):
                yield chunk
    except Exception as e:
        logger.error("[PHASE:2:DEEP_AGENT:UNRESOLVED] Neo4j 반영 실패 | error=%s", e, exc_info=True)


# ═══════════════════════════════════════════════════════════════════════
# Step 3: FK 반영 + 상태 저장
# ═══════════════════════════════════════════════════════════════════════

async def _run_post_linking(ctx: _LinkingContext, stats: AnalysisStats) -> AsyncGenerator[bytes, None]:
    """FK 엣지 Neo4j 반영 + 파이프라인 상태 저장."""
    st = ctx.st

    # FK 엣지 반영
    if ctx.fk_edges:
        try:
            fk_queries: list = []
            fk_queries.extend(build_fk_to_table_queries(ctx.fk_edges))
            fk_queries.extend(build_fk_to_column_queries(ctx.fk_edges))
            if fk_queries:
                logger.info("[PHASE:2:FK] FK 엣지 반영 | fk_edges=%d queries=%d", len(ctx.fk_edges), len(fk_queries))
                async for chunk in write_to_graph(ctx.client, fk_queries, "PHASE2:FK_EDGES"):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:2:FK] Neo4j FK 반영 실패 | error=%s", e, exc_info=True)

    # 배치별 Neo4j 반영은 _run_deterministic_and_llm_batch에서 이미 처리됨.
    # 여기서는 상태 저장만 수행.

    # 상태 저장
    stats.total_nodes += len(ctx.refer_to_edges) + len(ctx.all_calls)
    st.refer_to_edges = ctx.refer_to_edges
    st.reads_writes = ctx.reads_writes
    st.fk_edges = ctx.fk_edges
    st.column_refs = ctx.column_refs
    st.function_table_edges = ctx.function_table_edges
    st.module_table_edges = ctx.module_table_edges
    st.unresolved = ctx.unresolved
    st.calls_edges_from_batch = ctx.calls_edges_from_batch
    st.unresolved_calls = ctx.unresolved_calls
    st.calls_edges = ctx.all_calls
    st.uses_edges = ctx.uses_edges
    st.table_descriptions = ctx.table_descriptions
