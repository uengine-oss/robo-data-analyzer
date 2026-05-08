"""Phase 2: 참조 해석 — 파이프라인 오케스트레이션.

본 파일은 매니저 (호출/반복/sequencing/yield) 만 담당.
비즈니스 로직은 도메인 모듈에서 import:
  - linking/reference_linker.py: link_chunks_to_indexes, resolve_candidates,
    derive_function_table_edges, augment_table_chunks_with_entities,
    derive_module_table_edges_from_entity_keys, derive_artifact_table_edges_from_entity_keys
  - linking/call_linker.py: derive_uses_from_calls, run_call_batch_pipeline_async
  - linking/table_linker.py: link_sql_to_tables_async
  - linking/entity_linker.py: collect_entity_startpoints, run_entity_identification,
    collect_entity_fqns_from_edges
  - linking/unresolved_linker.py: run_unresolved_linking
  - common.pipeline.variable_linker / init_by_linker: 결정론 변수 매칭 (이전 linking/variable_linker.py 흡수)

오케스트레이션 4 단계:
  1. run_deterministic_and_llm_batch — 결정론 매칭 + Table/Call LLM 배치 + 후처리
  2. run_deep_agent_parallel — DeepAgent 3종 병렬 (entity / var_refs / unresolved)
  3. run_post_linking — FK 반영 + 상태 저장
  4. run_phase_linking — 위 3 단계 sequencing
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.common.pipeline.init_by_linker import find_init_by_sources
from analyzer.common.pipeline.variable_linker import (
    WILDCARD_SCOPE as COMMON_WILDCARD_SCOPE,
    find_variable_references,
)
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.framework.analysis.chunk_bundle import (
    _get_chunk_bundle_dir_from_run_dir,
    build_bundle_span_index,
)
from analyzer.strategy.framework.common.cypher.graph_queries import (
    build_artifact_module_refer_to_queries,
    build_artifact_table_refer_to_queries,
    build_calls_queries,
    build_column_queries,
    build_fk_to_column_queries,
    build_fk_to_table_queries,
    build_has_column_queries,
    build_init_by_queries,
    build_module_table_refer_to_queries,
    build_node_table_queries,
    build_reads_writes_queries,
    build_refer_to_queries,
    build_table_from_entity_classes,
    build_table_queries_from_reads_writes,
    build_uses_queries,
    build_var_refs_queries,
)
from analyzer.strategy.framework.common.models.node_models import (
    ArtifactChunk,
    CallsEdge,
    ColumnRef,
    ConfigSectionChunk,
    FkEdge,
    FunctionFacts,
    InitByEdge,
    ReadsWritesEdge,
    SqlStatementChunk,
    VariableFacts,
)
from analyzer.strategy.framework.linking.call_linker import (
    derive_uses_from_calls,
    run_call_batch_pipeline_async,
    set_active_codebase_pattern,
)
from analyzer.strategy.framework.linking.entity_linker import (
    collect_entity_fqns_from_edges,
    collect_entity_startpoints,
    run_entity_identification,
)
from analyzer.strategy.framework.linking.reference_linker import (
    augment_table_chunks_with_entities,
    derive_artifact_table_edges_from_entity_keys,
    derive_function_table_edges,
    derive_module_table_edges_from_entity_keys,
    link_chunks_to_indexes,
    resolve_candidates,
)
from analyzer.strategy.framework.linking.table_linker import link_sql_to_tables_async
from analyzer.strategy.framework.linking.unresolved_linker import run_unresolved_linking
from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_emitter import PhaseTracker, emit_user_message

logger = logging.getLogger(__name__)


def _ensure_chunk_index_in_state(st: Any, data_dir: str) -> Dict[str, Any]:
    """span/chunk index 가 state 에 없으면 빌드. (이전: variable_linker._ensure_chunk_index)"""
    if getattr(st, "span_index", None) and getattr(st, "chunk_index", None):
        return st.chunk_index
    run_dir = getattr(st, "run_dir", None)
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir)
    span_index, chunk_index = build_bundle_span_index(
        chunk_bundle_dir=chunk_dir,
        artifact_index=getattr(st, "artifact_index", None),
        sql_index=getattr(st, "sql_index", None),
    )
    st.span_index = span_index
    st.chunk_index = chunk_index
    return chunk_index


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 내부 컨텍스트 — Step 1/2/3 공유 데이터
# ═══════════════════════════════════════════════════════════════════════════


class LinkingContext:
    """Phase 2 내부 누적 상태. Step 1 → Step 2 → Step 3 간 공유."""

    __slots__ = (
        "st", "tracker", "client", "data_dir", "api_key",
        "refer_to_edges", "reads_writes", "fk_edges", "column_refs",
        "function_table_edges", "module_table_edges", "artifact_table_edges",
        "unresolved", "all_calls", "calls_edges_from_batch",
        "unresolved_calls", "uses_edges", "table_descriptions",
        "table_batch_count", "call_batch_count",
    )

    def __init__(self, st: Any, tracker: PhaseTracker, client: Neo4jClient):
        self.st = st
        self.tracker = tracker
        self.client = client
        self.data_dir = st.data_dir or settings.path.data_dir
        self.api_key = settings.llm.api_key or os.environ.get("LLM_API_KEY", "")

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


# ═══════════════════════════════════════════════════════════════════════════
# 오케스트레이션 헬퍼: 결정론 매칭 + LLM 배치 (단계별 yield)
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_references(
    function_facts_list: List[FunctionFacts],
    sql_index: Dict[str, SqlStatementChunk],
    sql_chunks: List[SqlStatementChunk],
    config_index: Optional[Dict[str, ConfigSectionChunk]] = None,
    ref_candidates_by_function: Optional[Dict[str, List[Any]]] = None,
    symbol_type_index: Optional[Dict[str, Dict[str, str]]] = None,
    module_index: Optional[Dict[str, Any]] = None,
    data_dir: Optional[str] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    function_index_by_module: Optional[Dict[str, Dict[str, List[str]]]] = None,
    run_dir: str = "",
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
    artifact_chunks: Optional[List[ArtifactChunk]] = None,
    ddl_table_metadata: Optional[Dict[str, Any]] = None,
    lang: str = "",
) -> AsyncGenerator[Dict[str, Any], None]:
    """참조 해석 — 단계별 yield (호출자가 dict.get('stage') 로 분기).

    Yields:
        stage="deterministic": refer_to_edges, unresolved
        stage="table_batch":   edges, fk, col, desc
        stage="call_batch":    edges, unresolved
        stage="post":          reads_writes, fk_edges, column_refs, function/module/artifact_table_edges
    """
    if not run_dir:
        raise ValueError("run_dir 필수 - Phase 1 에서 초기화된 run_dir 을 전달해야 함")

    config_count = len(config_index) if config_index else 0
    logger.info(
        "[PHASE:2:LINKING] 링킹 진입  function_facts=%d sql_index=%d sql_chunks=%d config_index=%d",
        len(function_facts_list), len(sql_index), len(sql_chunks), config_count,
    )
    logger.info("[PHASE:2:LINKING] [매칭] 결정론 매칭 (키 in 코드, sql/config/class/artifact 인덱스)")

    refer_to_edges, unresolved = link_chunks_to_indexes(
        function_facts_list,
        sql_index=sql_index,
        config_index=config_index,
        module_index=module_index,
        artifact_index=artifact_index,
    )
    det_matched = len(refer_to_edges)
    logger.info(
        "[PHASE:2:LINKING] link_chunks_to_indexes 완료  refer_to=%d  미연결=%d",
        det_matched, len(unresolved),
    )

    if ref_candidates_by_function:
        function_index_by_id: Dict[str, FunctionFacts] = {m.function_id: m for m in function_facts_list}
        rc_matched = 0
        rc_unresolved = 0
        for func_id, cands in ref_candidates_by_function.items():
            mf = function_index_by_id.get(func_id)
            caller_fqn = mf.fqn if mf else func_id
            code_ref = mf.code_ref if mf else None
            add_edges, add_unresolved = resolve_candidates(
                func_id, caller_fqn, code_ref, cands,
                sql_index=sql_index, config_index=config_index, module_index=module_index,
                symbol_type_index=symbol_type_index or {}, artifact_index=artifact_index,
            )
            if add_edges:
                refer_to_edges.extend(add_edges)
                rc_matched += len(add_edges)
            if add_unresolved:
                unresolved.extend(add_unresolved)
                rc_unresolved += len(add_unresolved)
        logger.info(
            "[PHASE:2:LINKING] resolve_candidates 완료  매칭=%d  미해결=%d",
            rc_matched, rc_unresolved,
        )

    yield {"stage": "deterministic", "refer_to_edges": refer_to_edges, "unresolved": unresolved}

    # ─── 엔티티 청크 합류 (DDL 없을 때 외부파일·어노테이션 entity → SQL LLM 입력) ───
    api_key = settings.llm.api_key or os.environ.get("LLM_API_KEY", "")
    combined_table_chunks = augment_table_chunks_with_entities(
        sql_chunks, module_index, variable_facts_list, artifact_index, artifact_chunks, ddl_table_metadata,
    )

    # ─── 병렬 LLM 실행 (Table + Call) ───
    logger.info("[PHASE:2:LINKING] [2갈래] SQL→테이블 / Call LLM 병렬 실행 (배치별 점진 전송)")
    result_q: asyncio.Queue = asyncio.Queue()

    reads_writes_list: List[ReadsWritesEdge] = []
    fk_edges_list: List[FkEdge] = []
    column_refs_list: List[ColumnRef] = []
    table_descriptions: Dict[Tuple[str, str], str] = {}
    calls_edges: List[CallsEdge] = []
    unresolved_calls: List[Any] = []

    async def _run_table_llm() -> None:
        try:
            await link_sql_to_tables_async(
                combined_table_chunks, api_key=api_key,
                on_batch=lambda e, fk, col, desc: result_q.put_nowait(
                    ("table_batch", {"edges": e, "fk": fk, "col": col, "desc": desc})
                ),
            )
        except Exception as ex:  # noqa: SILENT_FAIL — 자식 task 예외를 result_q 로 전달하면 메인 루프가 raise 처리. 메인이 단일 출처
            logger.error("[PHASE:2:TABLE_LLM] 예외 | error=%s", ex, exc_info=True)
            await result_q.put(("error", ex))
            return
        await result_q.put(("table_done", None))

    async def _run_call_llm() -> None:
        try:
            await run_call_batch_pipeline_async(
                data_dir or "", function_facts_list, variable_facts_list or [],
                symbol_type_index or {}, function_index_by_module or {},
                use_llm=True, api_key=api_key, run_dir=run_dir,
                on_batch=lambda edges, unresolved: result_q.put_nowait(
                    ("call_batch", {"edges": edges, "unresolved": unresolved})
                ),
                lang=lang,
            )
        except Exception as ex:  # noqa: SILENT_FAIL — 자식 task 예외를 result_q 로 전달하면 메인 루프가 raise 처리. 메인이 단일 출처
            logger.error("[PHASE:2:CALL_LLM] 예외 | error=%s", ex, exc_info=True)
            await result_q.put(("error", ex))
            return
        await result_q.put(("call_done", None))

    table_task = asyncio.create_task(_run_table_llm())
    call_task = asyncio.create_task(_run_call_llm())

    done_count = 0
    while done_count < 2:
        tag, data = await result_q.get()
        if tag == "error":
            table_task.cancel()
            call_task.cancel()
            raise RuntimeError(f"LLM 처리 실패: {data}") from (data if isinstance(data, Exception) else None)
        if tag.endswith("_done"):
            done_count += 1
            continue
        if tag == "table_batch":
            reads_writes_list.extend(data["edges"])
            fk_edges_list.extend(data["fk"])
            column_refs_list.extend(data["col"])
            for (s, n), d in (data["desc"] or {}).items():
                if d and (s, n) not in table_descriptions:
                    table_descriptions[(s, n)] = d
            yield {"stage": "table_batch", **data}
        elif tag == "call_batch":
            calls_edges.extend(data["edges"])
            unresolved_calls.extend(data["unresolved"])
            yield {"stage": "call_batch", **data}

    await asyncio.gather(table_task, call_task)

    # ─── 후처리: entity 키 기반 Module→Table / Artifact→Table 엣지 유도 ───
    reads_writes = list(reads_writes_list)
    module_table_edges = derive_module_table_edges_from_entity_keys(reads_writes)
    artifact_table_edges = derive_artifact_table_edges_from_entity_keys(reads_writes)
    function_table_edges = derive_function_table_edges(
        refer_to_edges, reads_writes, artifact_table_edges=artifact_table_edges,
    )

    logger.info(
        "[PHASE:2:LINKING] 링킹 완료  REFER_TO=%d  reads_writes=%d  Function→Table=%d  "
        "Module→Table=%d  Artifact→Table=%d  미연결=%d",
        len(refer_to_edges), len(reads_writes), len(function_table_edges),
        len(module_table_edges), len(artifact_table_edges), len(unresolved),
    )

    yield {
        "stage": "post",
        "reads_writes": reads_writes,
        "fk_edges": fk_edges_list,
        "column_refs": column_refs_list,
        "function_table_edges": function_table_edges,
        "module_table_edges": module_table_edges,
        "artifact_table_edges": artifact_table_edges,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Step 1: 결정론 매칭 + LLM 배치 + 후처리
# ═══════════════════════════════════════════════════════════════════════════


async def run_deterministic_and_llm_batch(ctx: LinkingContext) -> AsyncGenerator[bytes, None]:
    """결정론 매칭 → Table/Call LLM 배치 병렬 → 후처리 엣지 유도."""
    st = ctx.st
    sql_index = st.sql_index
    sql_chunks = st.sql_chunks

    async for step_result in _resolve_references(
        st.function_facts_list, sql_index, sql_chunks,
        config_index=st.config_index,
        ref_candidates_by_function=st.ref_candidates_by_function,
        symbol_type_index=st.symbol_type_index,
        module_index=st.module_index,
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
            queries = build_refer_to_queries(ctx.refer_to_edges)
            if queries:
                async for chunk in write_to_graph(ctx.client, queries, "PHASE2:DETERMINISTIC"):
                    yield chunk
            yield ctx.tracker.begin_step(0, 1)
            yield ctx.tracker.end_step(f"참조 {len(ctx.refer_to_edges)}건, 미해결 {len(ctx.unresolved)}건")
            est_batches = max(1, (len(sql_chunks) + 19) // 20 + (len(st.function_facts_list) + 19) // 20)
            yield ctx.tracker.begin_step(1, est_batches)

        elif stage == "table_batch":
            ctx.reads_writes.extend(step_result["edges"])
            ctx.fk_edges.extend(step_result["fk"])
            ctx.column_refs.extend(step_result["col"])
            ctx.table_descriptions.update(step_result["desc"] or {})
            ctx.table_batch_count += 1
            queries: list = []
            queries.extend(build_reads_writes_queries(step_result["edges"]))
            queries.extend(build_table_queries_from_reads_writes(
                step_result["edges"], table_descriptions=step_result["desc"] or {},
            ))
            if step_result["col"]:
                queries.extend(build_column_queries(step_result["col"]))
                queries.extend(build_has_column_queries(step_result["col"]))
            if queries:
                async for chunk in write_to_graph(ctx.client, queries, "PHASE2:TABLE_BATCH"):
                    yield chunk
            msg, evt = ctx.tracker.tick()
            yield msg
            yield evt

        elif stage == "call_batch":
            batch_call_edges = step_result["edges"]
            batch_call_unresolved = step_result["unresolved"]
            ctx.all_calls.extend(batch_call_edges)
            ctx.calls_edges_from_batch.extend(batch_call_edges)
            ctx.unresolved_calls.extend(batch_call_unresolved)
            uses_batch = derive_uses_from_calls(batch_call_edges, ctx.st.function_facts_list)
            ctx.uses_edges.extend(uses_batch)
            ctx.call_batch_count += 1
            queries = []
            queries.extend(build_calls_queries(batch_call_edges, lang=ctx.st.lang))
            queries.extend(build_uses_queries(uses_batch, lang=ctx.st.lang))
            if queries:
                async for chunk in write_to_graph(ctx.client, queries, "PHASE2:CALL_BATCH"):
                    yield chunk
            msg, evt = ctx.tracker.tick()
            yield msg
            yield evt

        elif stage == "post":
            yield ctx.tracker.end_step(
                f"Table LLM {ctx.table_batch_count}건, Call LLM {ctx.call_batch_count}건"
            )
            ctx.reads_writes = step_result["reads_writes"]
            ctx.fk_edges = step_result["fk_edges"]
            ctx.column_refs = step_result["column_refs"]
            ctx.function_table_edges = step_result["function_table_edges"]
            ctx.module_table_edges = step_result["module_table_edges"]
            ctx.artifact_table_edges = step_result["artifact_table_edges"]
            queries = []
            queries.extend(build_node_table_queries(ctx.function_table_edges))
            queries.extend(build_module_table_refer_to_queries(ctx.module_table_edges))
            queries.extend(build_artifact_table_refer_to_queries(ctx.artifact_table_edges))
            queries.extend(build_artifact_module_refer_to_queries(
                st.artifact_chunks or [], st.module_index or {},
            ))
            if queries:
                async for chunk in write_to_graph(ctx.client, queries, "PHASE2:POST"):
                    yield chunk
            yield ctx.tracker.begin_step(2, 1)
            yield ctx.tracker.end_step(
                f"Function→Table {len(ctx.function_table_edges)}건, "
                f"Module→Table {len(ctx.module_table_edges)}건, "
                f"Artifact→Table {len(ctx.artifact_table_edges)}건"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Step 2: DeepAgent 3종 병렬 + 결과 dispatch
# ═══════════════════════════════════════════════════════════════════════════


async def run_deep_agent_parallel(ctx: LinkingContext) -> AsyncGenerator[bytes, None]:
    """엔티티 식별 + 변수 참조 + 미해결 참조 추적 — 3종 병렬 실행."""
    st = ctx.st
    ddl_meta = st.ddl_table_metadata or {}

    _SENTINEL = object()
    result_queue: asyncio.Queue = asyncio.Queue()

    def _run_entity_identification():
        codebase_pattern = (st.codebase_profile.pattern if st.codebase_profile else "")
        startpoints = collect_entity_startpoints(
            module_index=st.module_index or {},
            artifact_index=st.artifact_index or {},
            ddl_table_metadata=ddl_meta or None,
            codebase_pattern=codebase_pattern,
        )
        logger.info(
            "[PHASE:2:ENTITY] startpoints | ddl=%d xml=%d annotated=%d profile=%s",
            len(startpoints.ddl_tables), len(startpoints.xml_entity_fqns),
            len(startpoints.annotated_fqns),
            bool(startpoints.codebase_pattern),
        )
        entity_state = {
            "sql_index": st.sql_index,
            "config_index": st.config_index or {},
            "artifact_index": st.artifact_index or {},
            "data_dir": ctx.data_dir,
            "run_dir": str(Path(st.run_dir)) if st.run_dir else None,
            "span_index": st.span_index,
            "chunk_index": st.chunk_index,
            "module_index": st.module_index or {},
            "lang": st.lang,
            "ddl_table_metadata": ddl_meta or None,
            "codebase_pattern": codebase_pattern,
            "function_count": len(st.function_facts_list or []),
        }
        mt_edges, fk_e, col_refs = run_entity_identification(
            startpoints=startpoints, state=entity_state,
            api_key=settings.llm.api_key,
            run_dir=Path(st.run_dir) if st.run_dir else None,
        )
        logger.info(
            "[PHASE:2:ENTITY] 완료 | tables=%d fk=%d columns=%d",
            len(mt_edges), len(fk_e), len(col_refs),
        )
        result_queue.put_nowait(("entity", entity_state, mt_edges, fk_e, col_refs))

    def _run_variable_refs():
        """변수 참조 + INIT_BY 결정론적 매칭 (LLM 미사용). common.pipeline 호출."""
        variables = st.variable_facts_list or []
        if not variables:
            return
        logger.info("[PHASE:2:VARREF] 결정론적 매칭 시작 | variables=%d", len(variables))

        # ── REFERENCES: variable_facts + 검색 source chunks → (source_id, variable_id) 페어 ──
        # scope_id 정책 (find_variable_references 가 매칭에 사용):
        #   - VARIABLE / PARAMETER (함수 스코프) → scope_id = v.module_key (function_id).
        #     자기 함수 chunk 와만 매칭됨 — 외부 함수의 동명 PARAMETER/VARIABLE false-positive 차단.
        #   - GLOBAL_VARIABLE / DEFINE / CONSTANT_FIELD (모듈/전역 스코프) → 와일드카드.
        #     모든 source 에서 reference 가능 (struct 멤버 배열 안의 매크로 포함).
        # source: function chunk + member_var chunk (ontology 의 variable 역할 동등 취급).
        #   - function chunk : function_id (점 표기, `#`/`C:` 없음)
        #   - member_var chunk: variable_id (`#` 포함)
        #   - 제외: function_call/local_var initializer chunk (`C:` 접두 — 함수 본문에 이미 포함됨)
        _SCOPE_WILDCARD_KINDS = ("GLOBAL_VARIABLE", "DEFINE", "CONSTANT_FIELD")
        chunk_index = _ensure_chunk_index_in_state(st, ctx.data_dir)
        searchable_source_ids = frozenset(
            cid for cid in (chunk_index or {}) if not cid.startswith("C:")
        )
        targets = [
            {
                "id": v.variable_id, "name": v.name,
                "scope_id": COMMON_WILDCARD_SCOPE if v.kind in _SCOPE_WILDCARD_KINDS else v.module_key,
            }
            for v in variables if v.variable_id and v.name
        ]
        sources = [
            {"id": cid, "scope_id": cid, "code": (info.get("text") or "")}
            for cid, info in (chunk_index or {}).items() if cid in searchable_source_ids
        ]
        ref_pairs = find_variable_references(targets=targets, sources=sources)
        refs = [(s["id"], t["id"]) for t, s in ref_pairs]
        logger.info("[PHASE:2:VARREF] REFERENCES 매칭 완료 | refs=%d", len(refs))
        if refs:
            result_queue.put_nowait(("var_refs", refs))

        # ── INIT_BY: VariableFacts.init_value 텍스트 → 식별자 추출 (common 모듈) → InitByEdge ──
        init_variables: List[Dict[str, Any]] = [
            {"id": v.variable_id, "name": v.name, "init_value": v.init_value}
            for v in variables
            if v.variable_id and v.name and v.init_value
        ]
        name_index: Dict[str, List[Dict[str, Any]]] = {}
        for v in variables:
            if v.variable_id and v.name:
                name_index.setdefault(v.name.lower(), []).append({"id": v.variable_id, "name": v.name})
        init_pairs = find_init_by_sources(variables=init_variables, name_index=name_index)
        init_by = [
            InitByEdge(variable_id=var["id"], init_source_id=src["id"])
            for var, src in init_pairs
        ]
        if init_by:
            result_queue.put_nowait(("init_by", init_by))

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
        codebase_pattern = st.codebase_profile.pattern if st.codebase_profile else ""
        for item_refer, item_mt, item_fk in run_unresolved_linking(
            ctx.unresolved, deep_state, api_key=settings.llm.api_key,
            run_dir=run_dir_path, codebase_pattern=codebase_pattern,
        ):
            result_queue.put_nowait(("da_item", item_refer, item_mt, item_fk))
        result_queue.put_nowait(("da_state", deep_state))
        logger.info("[PHASE:2:DEEP_AGENT:UNRESOLVED] 완료")

    async def _run_all():
        await asyncio.gather(
            asyncio.to_thread(_run_entity_identification),
            asyncio.to_thread(_run_variable_refs),
            asyncio.to_thread(_run_unresolved_tracking),
        )
        await result_queue.put(_SENTINEL)

    parallel_task = asyncio.ensure_future(_run_all())

    yield ctx.tracker.begin_step(3, max(1, len(ctx.unresolved)))
    da_refer_all: List[Any] = []
    da_mt_all: List[Any] = []
    da_fk_all: List[Any] = []

    while True:
        item = await result_queue.get()
        if item is _SENTINEL:
            break
        kind = item[0]

        if kind == "entity":
            async for chunk in _handle_entity_result(ctx, item, ddl_meta):
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
        da_derived = derive_function_table_edges(
            da_refer_all, ctx.reads_writes,
            artifact_table_edges=ctx.artifact_table_edges,
        )
        if da_derived:
            ctx.function_table_edges.extend(da_derived)
            dq = build_node_table_queries(da_derived)
            if dq:
                async for chunk in write_to_graph(ctx.client, dq, "PHASE2:DEEPAGENT_DERIVED"):
                    yield chunk

    yield ctx.tracker.end_step(f"DeepAgent 완료 | REFER_TO {len(da_refer_all)}건")


# ═══════════════════════════════════════════════════════════════════════════
# DeepAgent 결과 핸들러 (Step 2 내부)
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_entity_result(ctx: LinkingContext, item: tuple, ddl_meta: dict) -> AsyncGenerator[bytes, None]:
    _, entity_state, entity_mt_edges, entity_fk_edges, entity_columns = item
    if entity_fk_edges:
        ctx.fk_edges.extend(entity_fk_edges)
    if entity_columns:
        ctx.column_refs.extend(entity_columns)
    if entity_mt_edges:
        ctx.function_table_edges.extend(entity_mt_edges)
        if not ddl_meta:
            entity_map = collect_entity_fqns_from_edges(entity_mt_edges, ctx.st.module_index or {})
            if entity_map:
                eq = build_table_from_entity_classes(
                    entity_map, ctx.st.module_index or {},
                    variable_facts_list=ctx.st.variable_facts_list or [],
                )
                if eq:
                    async for chunk in write_to_graph(ctx.client, eq, "PHASE2:ENTITY_TABLE"):
                        yield chunk
        eq2 = build_node_table_queries(entity_mt_edges)
        if eq2:
            async for chunk in write_to_graph(ctx.client, eq2, "PHASE2:ENTITY_ID"):
                yield chunk
    if entity_columns:
        cq = build_column_queries(entity_columns)
        cq.extend(build_has_column_queries(entity_columns))
        if cq:
            async for chunk in write_to_graph(ctx.client, cq, "PHASE2:ENTITY_COLUMN"):
                yield chunk
    if entity_state and entity_state.get("span_index") and ctx.st.span_index is None:
        ctx.st.span_index = entity_state["span_index"]
    if entity_state and entity_state.get("chunk_index") and ctx.st.chunk_index is None:
        ctx.st.chunk_index = entity_state["chunk_index"]


async def _handle_varref_result(ctx: LinkingContext, item: tuple) -> AsyncGenerator[bytes, None]:
    """REFERENCES UNWIND — graph_queries.build_var_refs_queries 호출."""
    refs = item[1]
    if not refs:
        return
    queries = build_var_refs_queries(refs)
    async for chunk in write_to_graph(ctx.client, queries, "PHASE2:VAR_REFS"):
        yield chunk
    logger.info("[PHASE:2:DEEP_AGENT:VARREF] 변수 참조 생성 | refs=%d", len(refs))


async def _handle_init_by_result(ctx: LinkingContext, item: tuple) -> AsyncGenerator[bytes, None]:
    init_by_edges = item[1]
    iq = build_init_by_queries(init_by_edges)
    if iq:
        async for chunk in write_to_graph(ctx.client, iq, "PHASE2:INIT_BY"):
            yield chunk
    logger.info("[PHASE:2:INIT_BY] 초기화 관계 생성 | edges=%d", len(init_by_edges))


async def _handle_da_item_result(
    ctx: LinkingContext, item_refer: list, item_mt: list,
) -> AsyncGenerator[bytes, None]:
    yield emit_user_message(
        f"  ├─ DeepAgent 미해결 추적 결과 ({len(item_refer)}개 참조, {len(item_mt)}개 테이블 접근)"
    )
    dq: list = []
    if item_refer:
        dq.extend(build_refer_to_queries(item_refer))
    if item_mt:
        dq.extend(build_node_table_queries(item_mt))
    if dq:
        async for chunk in write_to_graph(ctx.client, dq, "PHASE2:DEEPAGENT"):
            yield chunk


# ═══════════════════════════════════════════════════════════════════════════
# Step 3: FK 반영 + 상태 저장
# ═══════════════════════════════════════════════════════════════════════════


async def run_post_linking(ctx: LinkingContext, stats: AnalysisStats) -> AsyncGenerator[bytes, None]:
    """FK 엣지 Neo4j 반영 + 파이프라인 상태 저장."""
    st = ctx.st

    if ctx.fk_edges:
        fk_queries: list = []
        fk_queries.extend(build_fk_to_table_queries(ctx.fk_edges))
        fk_queries.extend(build_fk_to_column_queries(ctx.fk_edges))
        if fk_queries:
            logger.info(
                "[PHASE:2:FK] FK 엣지 반영 | fk_edges=%d queries=%d",
                len(ctx.fk_edges), len(fk_queries),
            )
            async for chunk in write_to_graph(ctx.client, fk_queries, "PHASE2:FK_EDGES"):
                yield chunk

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


# ═══════════════════════════════════════════════════════════════════════════
# 메인 — Phase 2 entry point
# ═══════════════════════════════════════════════════════════════════════════


async def run_phase_linking(
    ctx: Any,  # FrameworkPhaseContext
) -> AsyncGenerator[bytes, None]:
    """Phase 2: 참조 해석 → LLM 배치 → DeepAgent → FK 반영 (3 단계)."""
    st = ctx.state
    client = ctx.client
    stats = ctx.stats

    yield emit_user_message("🔗 링킹 시작 (참조 해석 → SQL→Table → Call → DeepAgent)")

    # call_extract LLM 호출에 주입할 codebase_pattern 캐시 — Phase 2 진입 시점 1회 설정.
    # _ACTIVE_LANG/_ACTIVE_STDLIB 와 동일한 글로벌 캐시 패턴.
    set_active_codebase_pattern(st.codebase_profile.pattern if st.codebase_profile else "")

    tracker = PhaseTracker(1, "LLM 링킹", total_phases=3)
    tracker.define_steps([
        ("결정론적 링킹", 10),
        ("LLM 배치 링킹", 55),
        ("후처리", 10),
        ("DeepAgent 의미 추적", 25),
    ])

    link_ctx = LinkingContext(st, tracker, client)

    # Step 1: 결정론적 매칭 + LLM 배치 + 후처리 엣지 유도
    async for chunk in run_deterministic_and_llm_batch(link_ctx):
        yield chunk

    # Step 2: DeepAgent 3종 병렬
    async for chunk in run_deep_agent_parallel(link_ctx):
        yield chunk

    # Step 3: FK 반영 + 상태 저장
    async for chunk in run_post_linking(link_ctx, stats):
        yield chunk

    yield emit_user_message(
        f"✅ Phase 2 완료 | 참조 {len(link_ctx.refer_to_edges)}건, "
        f"CALLS {len(link_ctx.all_calls)}건, "
        f"Table배치 {link_ctx.table_batch_count}건, Call배치 {link_ctx.call_batch_count}건"
    )
