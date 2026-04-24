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
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from util.stream_emitter import emit_message, emit_data, PhaseTracker
from analyzer.strategy.framework.analysis.chunk_bundle import (
    _get_chunk_bundle_dir_from_run_dir,
)
from analyzer.strategy.framework.linking.call_linker import derive_uses_from_calls
from analyzer.strategy.framework.common.node_models import (
    CallsEdge, ReferToEdge, ReadsWritesEdge, FkEdge, ColumnRef,
    FunctionFacts, VariableFacts, SqlStatementChunk, ConfigSectionChunk, ArtifactChunk,
    FunctionTableEdge, ModuleTableEdge, ArtifactTableEdge, UnresolvedLink,
    UsesEdge,
)
from analyzer.strategy.framework.linking.table_linker import sql_op_to_method_relation as _sql_op_to_function_relation
from analyzer.strategy.framework.linking.entity_startpoints import identify_entity_classes
from analyzer.strategy.framework.linking.reference_linker import (
    link_chunks_to_indexes,
    resolve_candidates,
)
from analyzer.strategy.framework.common.graph_queries import (
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
from analyzer.common.graph_writer import run_graph_queries, write_to_graph
from analyzer.strategy.framework.linking.agent.unresolved_agent import run_deep_agent
from analyzer.strategy.framework.linking.agent.entity_agent import run_entity_identification
from analyzer.strategy.framework.linking.variable_linker import run_variable_references
from analyzer.strategy.framework.linking.entity_startpoints import collect_entity_startpoints

logger = logging.getLogger(__name__)


# ===========================================================================
# derive_function_table_edges: REFER_TO + reads_writes 체인으로 Function→Table 유도
# (구 linking/orchestrator.py에서 병합)
# ===========================================================================


def derive_function_table_edges(
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    artifact_table_edges: Optional[List[ArtifactTableEdge]] = None,
) -> List[FunctionTableEdge]:
    """Function→SqlStatement/ArtifactChunk + reads_writes/artifact_table 체인으로 Function→Table(READS/WRITES) 자동 유도.

    1) Function─REFER_TO→SqlStatement + SqlStatement→Table(reads_writes) → Function─READS/WRITES→Table
    2) Function─REFER_TO→ArtifactChunk + ArtifactChunk→Table(artifact_table_edges) → Function─READS/WRITES→Table
    """
    result: List[FunctionTableEdge] = []
    seen: set = set()

    # ── 경로 1) SQL: SqlStatement → Table ──
    sql_to_tables: Dict[str, List[Tuple[str, str, str]]] = {}
    for rw in reads_writes:
        if rw.sql_key not in sql_to_tables:
            sql_to_tables[rw.sql_key] = []
        sql_to_tables[rw.sql_key].append((rw.table_schema, rw.table_name, rw.relation))

    for edge in refer_to_edges:
        if edge.target_type != "SqlStatement":
            continue
        variable_id = getattr(edge, "variable_id", None)
        for schema, table, sql_rel in sql_to_tables.get(edge.target_id, []):
            fn_rel = _sql_op_to_function_relation(sql_rel)
            dedup_key = (edge.function_id, variable_id or "", schema, table, fn_rel)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(FunctionTableEdge(
                function_id=edge.function_id, table_schema=schema,
                table_name=table, relation=fn_rel, variable_id=variable_id,
            ))

    # ── 경로 2) Artifact: ArtifactChunk → Table ──
    if artifact_table_edges:
        chunk_to_tables: Dict[str, List[Tuple[str, str]]] = {}
        for at in artifact_table_edges:
            cid = (at.chunk_id or "").strip()
            if cid:
                chunk_to_tables.setdefault(cid, []).append((at.table_schema, at.table_name))

        for edge in refer_to_edges:
            if edge.target_type != "ArtifactChunk":
                continue
            variable_id = getattr(edge, "variable_id", None)
            for schema, table in chunk_to_tables.get(edge.target_id, []):
                # ArtifactChunk 경로는 기본 READS (CRUD 세분화는 deepagent confidence에 의존)
                fn_rel = "READS"
                dedup_key = (edge.function_id, variable_id or "", schema, table, fn_rel)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                result.append(FunctionTableEdge(
                    function_id=edge.function_id, table_schema=schema,
                    table_name=table, relation=fn_rel, variable_id=variable_id,
                ))

    return result


# ===========================================================================
# resolve_references: 링킹 오케스트레이션 (결정론 매칭 → 병렬 LLM → 후처리)
# (구 linking/orchestrator.py에서 병합)
# ===========================================================================


async def resolve_references(
    function_facts_list: List[FunctionFacts],
    sql_index: Dict[str, SqlStatementChunk],
    sql_chunks: List[SqlStatementChunk],
    config_index: Optional[Dict[str, ConfigSectionChunk]] = None,
    ref_candidates_by_function: Optional[Dict[str, List[Any]]] = None,
    symbol_type_index: Optional[Dict[str, Dict[str, str]]] = None,
    module_index: Optional[Dict[str, Any]] = None,
    field_chunks: Optional[List[Dict[str, Any]]] = None,
    data_dir: Optional[str] = None,
    variable_facts_list: Optional[List[VariableFacts]] = None,
    function_index_by_module: Optional[Dict[str, Dict[str, List[str]]]] = None,
    run_dir: str = "",
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
    artifact_chunks: Optional[List[ArtifactChunk]] = None,
    ddl_table_metadata: Optional[Dict[str, Any]] = None,
    lang: str = "",
) -> AsyncGenerator[Dict[str, Any], None]:
    """참조 해석 — 단계별로 결과를 async yield.

    Yields:
        stage="deterministic": refer_to_edges, unresolved
        stage="table_batch": edges, fk, col, desc (배치별)
        stage="call_batch": edges, unresolved (배치별)
        stage="post": reads_writes(final), function_table_edges, module_table_edges, artifact_table_edges
    """
    if not run_dir:
        raise ValueError("run_dir 필수 - Phase 1 에서 초기화된 run_dir 을 전달해야 함")

    # ===================================================================
    # 1) 결정론적 매칭 (키 in 코드, sql/config/class/artifact 인덱스)
    # ===================================================================

    config_count = len(config_index) if config_index else 0
    logger.info(
        "[PHASE:2:LINKING] 링킹 진입  function_facts=%d sql_index=%d sql_chunks=%d config_index=%d variable_chunks=%s",
        len(function_facts_list),
        len(sql_index),
        len(sql_chunks),
        config_count,
        len(field_chunks) if field_chunks else 0,
    )
    logger.info("[PHASE:2:LINKING] [매칭] 결정론 매칭 (키 in 코드, sql/config/class/artifact 인덱스)")

    refer_to_edges, unresolved = link_chunks_to_indexes(
        function_facts_list,
        field_chunks=field_chunks,
        sql_index=sql_index,
        config_index=config_index,
        module_index=module_index,
        artifact_index=artifact_index,
    )
    det_matched = len(refer_to_edges)
    logger.info(
        "[PHASE:2:LINKING] link_chunks_to_indexes 완료  refer_to=%d  미연결=%d",
        det_matched,
        len(unresolved),
    )

    # ── ref_candidates_by_function 기반 결정론 해석 (CALL_REF / NEW_INSTANCE_REF) ──
    if ref_candidates_by_function:
        function_index: Dict[str, FunctionFacts] = {m.function_id: m for m in function_facts_list}
        rc_matched = 0
        rc_unresolved = 0
        for func_id, cands in ref_candidates_by_function.items():
            mf = function_index.get(func_id)
            caller_fqn = getattr(mf, "fqn", "") if mf else func_id
            code_ref = getattr(mf, "code_ref", None) if mf else None
            add_edges, add_unresolved = resolve_candidates(
                func_id,
                caller_fqn,
                code_ref,
                cands,
                sql_index=sql_index,
                config_index=config_index,
                module_index=module_index,
                symbol_type_index=symbol_type_index or {},
                artifact_index=artifact_index,
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

    # ── yield 1: 결정론적 결과 ──
    yield {
        "stage": "deterministic",
        "refer_to_edges": refer_to_edges,
        "unresolved": unresolved,
    }

    # ===================================================================
    # 2) 엔티티 청크 준비 (DDL / 외부 XML·어노테이션 / DeepAgent 분기)
    # ===================================================================

    api_key = settings.llm.api_key or os.environ.get("LLM_API_KEY", "")
    combined_table_chunks: List[SqlStatementChunk] = list(sql_chunks or [])

    # DDL 없을 때, 외부파일(XML entity, 어노테이션) 존재하면 entity 청크를 table LLM 입력에 합류
    _has_entity_info = bool(identify_entity_classes(module_index or {})) or any(
        getattr(ac, "chunk_type", "") in ("entity", "cmp_mapping")
        for ac in (artifact_index or {}).values()
    )

    if _has_entity_info and not ddl_table_metadata:
        # 외부파일(XML entity, 어노테이션) 존재 → entity 청크를 table LLM 입력에 합류
        entity_fqns = identify_entity_classes(module_index or {})

        # 2-a) 어노테이션 기반 엔티티 → SqlStatementChunk 변환
        for fqn in entity_fqns:
            info = (module_index or {}).get(fqn)
            if not isinstance(info, dict):
                continue
            module_key = info.get("module_key") or fqn
            fields = [f for f in (variable_facts_list or []) if getattr(f, "module_key", "") == module_key]
            if not fields:
                continue
            field_text = "\n".join(
                f"{f.name} ({getattr(f, 'variable_type', '?')})"
                + (f" {f.annotations}" if getattr(f, "annotations", None) else "")
                for f in fields
            )
            combined_table_chunks.append(
                SqlStatementChunk(
                    sql_key=f"entity::{fqn}",
                    sql_type="entity_definition",
                    sql_text=field_text,
                    start_line=0,
                    end_line=0,
                    file_path="",
                )
            )

        # 2-b) XML(ejb-jar 등) 기반 엔티티 → SqlStatementChunk 변환
        for ac in (artifact_chunks or []):
            ct = getattr(ac, "chunk_type", "")
            if ct not in ("entity", "cmp_mapping"):
                continue
            text = (getattr(ac, "text", "") or "").strip()
            if not text:
                continue
            combined_table_chunks.append(
                SqlStatementChunk(
                    sql_key=f"entity_xml::{getattr(ac, 'chunk_id', '')}",
                    sql_type="entity_definition",
                    sql_text=text,
                    start_line=getattr(ac, "start_line", 0),
                    end_line=getattr(ac, "end_line", 0),
                    file_path=getattr(ac, "source_path", ""),
                )
            )

        entity_count = len(combined_table_chunks) - len(sql_chunks or [])
        if entity_count:
            logger.info(
                "[PHASE:2:LINKING] 엔티티 청크 %d개를 table LLM 입력에 합류 (sql=%d + entity=%d)",
                entity_count,
                len(sql_chunks or []),
                entity_count,
            )
    else:
        entity_fqns = set()
        if ddl_table_metadata:
            logger.info("[PHASE:2:LINKING] DDL 테이블 메타데이터 존재 — 엔티티 청크 생성 스킵")
        elif not _has_entity_info:
            logger.info("[PHASE:2:LINKING] 외부 엔티티 정보 없음 — DeepAgent 엔티티 식별 대상")

    # ===================================================================
    # 3) 병렬 LLM 실행 (Table LLM + Call LLM, queue 기반 점진 yield)
    # ===================================================================

    logger.info(
        "[PHASE:2:LINKING] [2갈래] SQL→테이블 / Call LLM 병렬 실행 (배치별 점진 전송)",
    )

    # 결과 수집 버퍼
    reads_writes_list: List[ReadsWritesEdge] = []
    fk_edges_list: List[FkEdge] = []
    column_refs_list: List[ColumnRef] = []
    table_descriptions: Dict[Tuple[str, str], str] = {}
    calls_edges: List[CallsEdge] = []
    unresolved_calls: List[Any] = []

    # ── asyncio.Queue 기반 병렬 실행 ──

    result_q: asyncio.Queue = asyncio.Queue()

    async def _run_table_llm() -> None:
        """Table LLM: SQL/엔티티 청크 → reads_writes, FK, 컬럼 추출 (async)."""
        try:
            from analyzer.strategy.framework.linking.table_linker import link_sql_to_tables_async
            await link_sql_to_tables_async(
                combined_table_chunks,
                api_key=api_key,
                on_batch=lambda e, fk, col, desc: result_q.put_nowait(
                    ("table_batch", {"edges": e, "fk": fk, "col": col, "desc": desc})
                ),
            )
        except Exception as ex:
            logger.error("[PHASE:2:TABLE_LLM] 예외 | error=%s", ex, exc_info=True)
            await result_q.put(("error", ex))
            return
        await result_q.put(("table_done", None))

    async def _run_call_llm() -> None:
        """Call LLM: 함수 간 호출 관계 추론 (async)."""
        try:
            from analyzer.strategy.framework.linking.call_linker import run_call_batch_pipeline_async
            await run_call_batch_pipeline_async(
                data_dir or "",
                function_facts_list,
                variable_facts_list or [],
                symbol_type_index or {},
                function_index_by_module or {},
                use_llm=True,
                api_key=api_key,
                run_dir=run_dir,
                on_batch=lambda edges, unresolved: result_q.put_nowait(
                    ("call_batch", {"edges": edges, "unresolved": unresolved})
                ),
                lang=lang,
            )
        except Exception as ex:
            logger.error("[PHASE:2:CALL_LLM] 예외 | error=%s", ex, exc_info=True)
            await result_q.put(("error", ex))
            return
        await result_q.put(("call_done", None))

    # ── 병렬 시작 + Queue 소비 ──

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

    # ===================================================================
    # 4) 후처리: entity 키 기반 Module→Table / Artifact→Table 엣지 유도
    # ===================================================================

    reads_writes = list(reads_writes_list)
    fk_edges = fk_edges_list
    column_refs = column_refs_list

    # ── entity::FQN → ModuleTableEdge ──
    seen_mt: Set[Tuple[str, str, str]] = set()
    module_table_edges: List[ModuleTableEdge] = []
    for rw in reads_writes:
        sql_key = (getattr(rw, "sql_key", "") or "").strip()
        if not sql_key.startswith("entity::"):
            continue
        module_fqn = sql_key.split("::", 1)[-1].strip()
        if not module_fqn:
            continue
        schema = (getattr(rw, "table_schema", "") or "").strip()
        name = (getattr(rw, "table_name", "") or "").strip()
        if not name:
            continue
        key = (module_fqn, schema, name)
        if key in seen_mt:
            continue
        seen_mt.add(key)
        module_table_edges.append(ModuleTableEdge(module_fqn=module_fqn, table_schema=schema, table_name=name))

    # ── entity_xml::chunk_id → ArtifactTableEdge ──
    seen_at: Set[Tuple[str, str, str]] = set()
    artifact_table_edges: List[ArtifactTableEdge] = []
    for rw in reads_writes:
        sql_key = (getattr(rw, "sql_key", "") or "").strip()
        if not sql_key.startswith("entity_xml::"):
            continue
        chunk_id = sql_key.split("::", 1)[-1].strip()
        if not chunk_id:
            continue
        schema = (getattr(rw, "table_schema", "") or "").strip()
        name = (getattr(rw, "table_name", "") or "").strip()
        if not name:
            continue
        key = (chunk_id, schema, name)
        if key in seen_at:
            continue
        seen_at.add(key)
        artifact_table_edges.append(ArtifactTableEdge(chunk_id=chunk_id, table_schema=schema, table_name=name))

    # ── Function→Table 자동 유도 (REFER_TO + reads_writes 체인) ──
    logger.info("[PHASE:2:LINKING] [Function-Table] REFER_TO + READS/WRITES 엣지 유도")
    function_table_edges = derive_function_table_edges(refer_to_edges, reads_writes, artifact_table_edges=artifact_table_edges)

    logger.info(
        "[PHASE:2:LINKING] 링킹 완료  REFER_TO=%d  reads_writes=%d  Function→Table=%d  Module→Table=%d  Artifact→Table=%d  미연결=%d",
        len(refer_to_edges),
        len(reads_writes),
        len(function_table_edges),
        len(module_table_edges),
        len(artifact_table_edges),
        len(unresolved),
    )

    # ── yield 4: 후처리 결과 ──
    yield {
        "stage": "post",
        "reads_writes": reads_writes,
        "fk_edges": fk_edges,
        "column_refs": column_refs,
        "function_table_edges": function_table_edges,
        "module_table_edges": module_table_edges,
        "artifact_table_edges": artifact_table_edges,
    }


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
    field_chunks = []  # 이전 하위호환 코드 제거됨 — fields 미사용
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

    _SENTINEL = object()
    result_queue: asyncio.Queue = asyncio.Queue()

    # ── 병렬 작업 정의 ──

    def _run_entity_identification():
        startpoints = collect_entity_startpoints(
            module_index=st.module_index or {},
            artifact_index=st.artifact_index or {},
            ddl_table_metadata=ddl_meta or None,
            profile_summary=(st.codebase_profile.summary if st.codebase_profile else ""),
        )
        logger.info(
            "[PHASE:2:ENTITY] startpoints | ddl=%d xml=%d annotated=%d candidates=%d profile=%s",
            len(startpoints.ddl_tables), len(startpoints.xml_entity_fqns),
            len(startpoints.annotated_fqns), len(startpoints.class_candidates),
            bool(startpoints.profile_summary),
        )
        entity_state = {
            "sql_index": st.sql_index,
            "config_index": st.config_index or {},
            "artifact_index": st.artifact_index or {},
            "data_dir": ctx.data_dir,
            "run_dir": str(Path(st.run_dir)) if st.run_dir else None,
            "span_index": st.span_index,
            "chunk_index": st.chunk_index,
            "ddl_table_metadata": ddl_meta or None,
            "codebase_profile_summary": (
                st.codebase_profile.summary if st.codebase_profile else ""
            ),
            "function_count": len(st.function_facts_list or []),
        }
        mt_edges, fk_e, col_refs = run_entity_identification(
            startpoints=startpoints,
            state=entity_state,
            api_key=settings.llm.api_key,
            run_dir=Path(st.run_dir) if st.run_dir else None,
        )
        logger.info(
            "[PHASE:2:ENTITY] 완료 | tables=%d fk=%d columns=%d",
            len(mt_edges), len(fk_e), len(col_refs),
        )
        result_queue.put_nowait(("entity", entity_state, mt_edges, fk_e, col_refs))

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
        from analyzer.strategy.framework.linking.variable_linker import build_init_by_edges
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
        profile_summary = st.codebase_profile.summary if st.codebase_profile else ""
        for item_refer, item_mt, item_fk in run_deep_agent(
            ctx.unresolved, deep_state, api_key=settings.llm.api_key, run_dir=run_dir_path,
            profile_summary=profile_summary,
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
    _, entity_state, entity_mt_edges, entity_fk_edges, entity_columns = item
    if entity_fk_edges:
        ctx.fk_edges.extend(entity_fk_edges)
    if entity_columns:
        ctx.column_refs.extend(entity_columns)
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
    if entity_columns:
        try:
            cq = build_column_queries(entity_columns)
            cq.extend(build_has_column_queries(entity_columns))
            if cq:
                async for chunk in write_to_graph(ctx.client, cq, "PHASE2:ENTITY_COLUMN"):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:2:DEEP_AGENT:ENTITY] Column Neo4j 반영 실패 | error=%s", e, exc_info=True)
    if entity_state and entity_state.get("span_index") and ctx.st.span_index is None:
        ctx.st.span_index = entity_state["span_index"]
    if entity_state and entity_state.get("chunk_index") and ctx.st.chunk_index is None:
        ctx.st.chunk_index = entity_state["chunk_index"]


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
        from analyzer.strategy.framework.common.graph_queries import build_init_by_queries
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
