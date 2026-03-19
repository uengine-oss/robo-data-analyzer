# ---------------------------------------------------------------------------
# Step 5 오케스트레이터: run_step5, derive_function_table_edges
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging
import os
import queue as queue_mod
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from config.settings import settings
from analyzer.strategy.framework.models import (
    CallsEdge,
    FunctionFacts,
    VariableFacts,
    SqlStatementChunk,
    ConfigSectionChunk,
    ArtifactChunk,
    ReferToEdge,
    ReadsWritesEdge,
    FkEdge,
    ColumnRef,
    FunctionTableEdge,
    ModuleTableEdge,
    ArtifactTableEdge,
    UnresolvedLink,
)
from analyzer.strategy.framework.common.utils import sql_op_to_method_relation as _sql_op_to_function_relation
from analyzer.strategy.framework.linking.matcher import (
    link_chunks_to_indexes,
    resolve_candidates,
    identify_entity_classes,
    has_external_entity_info,
)
from analyzer.strategy.framework.linking.call_graph import run_call_batch_pipeline

logger = logging.getLogger(__name__)


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

    # 1) SQL 경로: SqlStatement → Table
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
            function_rel = _sql_op_to_function_relation(sql_rel)
            dedup_key = (edge.function_id, variable_id or "", schema, table, function_rel)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(FunctionTableEdge(
                function_id=edge.function_id, table_schema=schema,
                table_name=table, relation=function_rel, variable_id=variable_id,
            ))

    # 2) Artifact 경로: ArtifactChunk → Table
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
                function_rel = "READS"
                dedup_key = (edge.function_id, variable_id or "", schema, table, function_rel)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                result.append(FunctionTableEdge(
                    function_id=edge.function_id, table_schema=schema,
                    table_name=table, relation=function_rel, variable_id=variable_id,
                ))

    return result


def run_step5(
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
    run_dir: Optional[str] = None,
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
    artifact_chunks: Optional[List[ArtifactChunk]] = None,
    ddl_table_metadata: Optional[Dict[str, Any]] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Step 5: 링킹 — 단계별로 결과를 yield.

    Yields:
        stage="deterministic": refer_to_edges, unresolved
        stage="table_llm": reads_writes, fk_edges, column_refs, table_descriptions
        stage="call_llm": calls_edges, unresolved_calls
        stage="post": reads_writes(final), function_table_edges, module_table_edges, artifact_table_edges
    """
    config_count = len(config_index) if config_index else 0
    logger.info(
        "[FW:STEP5] 링킹 진입  function_facts=%d sql_index=%d sql_chunks=%d config_index=%d variable_chunks=%s",
        len(function_facts_list),
        len(sql_index),
        len(sql_chunks),
        config_count,
        len(field_chunks) if field_chunks else 0,
    )
    logger.info("[FW:STEP5] [매칭] 결정론 매칭 (키 in 코드, sql/config/class/artifact 인덱스)")
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
        "[FW:STEP5] link_chunks_to_indexes 완료  refer_to=%d  미연결=%d",
        det_matched,
        len(unresolved),
    )

    # ref_candidates_by_function 기반 결정론 해석 (CALL_REF / NEW_INSTANCE_REF)
    if ref_candidates_by_function:
        function_index: Dict[str, FunctionFacts] = {m.function_id: m for m in function_facts_list}
        rc_matched = 0
        rc_unresolved = 0
        for mid, cands in ref_candidates_by_function.items():
            mf = function_index.get(mid)
            caller_fqn = getattr(mf, "fqn", "") if mf else mid
            code_ref = getattr(mf, "code_ref", None) if mf else None
            add_edges, add_unresolved = resolve_candidates(
                mid,
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
            "[FW:STEP5] resolve_candidates 완료  매칭=%d  미해결=%d",
            rc_matched, rc_unresolved,
        )

    # ── 1단계 yield: 결정론적 결과 ──
    yield {
        "stage": "deterministic",
        "refer_to_edges": refer_to_edges,
        "unresolved": unresolved,
    }

    api_key = settings.llm.api_key or os.environ.get("LLM_API_KEY", "")

    combined_table_chunks: List[SqlStatementChunk] = list(sql_chunks or [])

    # 3단계 우선순위: DDL → 외부파일(XML/어노테이션) → DeepAgent(phases.py에서 처리)
    _has_external = has_external_entity_info(
        artifact_index=artifact_index,
        module_index=module_index,
        ddl_table_metadata=ddl_table_metadata,
    )

    if _has_external and not ddl_table_metadata:
        # 외부파일(XML entity, 어노테이션) 존재 → entity 청크를 table LLM 입력에 합류
        entity_fqns = identify_entity_classes(module_index or {})
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
                "[FW:STEP5] 엔티티 청크 %d개를 table LLM 입력에 합류 (sql=%d + entity=%d)",
                entity_count,
                len(sql_chunks or []),
                entity_count,
            )
    else:
        entity_fqns = set()
        if ddl_table_metadata:
            logger.info("[FW:STEP5] DDL 테이블 메타데이터 존재 — 엔티티 청크 생성 스킵")
        elif not _has_external:
            logger.info("[FW:STEP5] 외부 엔티티 정보 없음 — DeepAgent 엔티티 식별 대상")

    logger.info(
        "[FW:STEP5] [3갈래] SQL→테이블 / Call LLM / UML 병렬 실행 (배치별 점진 전송)",
    )
    reads_writes_list: List[ReadsWritesEdge] = []
    fk_edges_list: List[FkEdge] = []
    column_refs_list: List[ColumnRef] = []
    table_descriptions: Dict[Tuple[str, str], str] = {}
    calls_edges: List[CallsEdge] = []
    unresolved_calls: List[Any] = []

    # queue 기반 병렬: 각 LLM 배치 완료 즉시 yield
    result_q: queue_mod.Queue = queue_mod.Queue()

    def _run_table_thread() -> None:
        try:
            from analyzer.strategy.framework.linking.sql_extractor import link_sql_to_tables
            link_sql_to_tables(
                combined_table_chunks,
                api_key=api_key,
                on_batch=lambda e, fk, col, desc: result_q.put(
                    ("table_batch", {"edges": e, "fk": fk, "col": col, "desc": desc})
                ),
            )
        except Exception as ex:
            logger.warning("[FW:STEP5] Table LLM 스레드 예외: %s", ex)
        result_q.put(("table_done", None))

    def _run_call_thread() -> None:
        try:
            run_call_batch_pipeline(
                data_dir or "",
                function_facts_list,
                variable_facts_list or [],
                symbol_type_index or {},
                function_index_by_module or {},
                use_llm=True,
                api_key=api_key,
                run_dir=run_dir,
                on_batch=lambda edges, unresolved: result_q.put(
                    ("call_batch", {"edges": edges, "unresolved": unresolved})
                ),
            )
        except Exception as ex:
            logger.warning("[FW:STEP5] Call LLM 스레드 예외: %s", ex)
        result_q.put(("call_done", None))

    t_table = threading.Thread(target=_run_table_thread, daemon=True)
    t_call = threading.Thread(target=_run_call_thread, daemon=True)
    t_table.start()
    t_call.start()

    done_count = 0
    while done_count < 2:
        try:
            tag, data = result_q.get(timeout=1.0)
        except queue_mod.Empty:
            continue
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

    t_table.join()
    t_call.join()

    # ── 후처리: edge 유도 ──
    reads_writes = list(reads_writes_list)
    fk_edges = fk_edges_list
    column_refs = column_refs_list

    # entity::FQN → (MODULE)-[:REFER_TO]->(TABLE)
    seen_ct: Set[Tuple[str, str, str]] = set()
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
        if key in seen_ct:
            continue
        seen_ct.add(key)
        module_table_edges.append(ModuleTableEdge(module_fqn=module_fqn, table_schema=schema, table_name=name))

    # entity_xml::chunk_id → (ARTIFACT_CHUNK)-[:REFER_TO]->(TABLE)
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

    # Function→Table 자동 유도
    logger.info("[FW:STEP5] [Function–Table] REFER_TO + READS/WRITES 엣지 유도")
    function_table_edges = derive_function_table_edges(refer_to_edges, reads_writes, artifact_table_edges=artifact_table_edges)

    logger.info(
        "[FW:STEP5] 링킹 완료  REFER_TO=%d  reads_writes=%d  Function→Table=%d  Module→Table=%d  Artifact→Table=%d  미연결=%d",
        len(refer_to_edges),
        len(reads_writes),
        len(function_table_edges),
        len(module_table_edges),
        len(artifact_table_edges),
        len(unresolved),
    )

    # ── 4단계 yield: 후처리 결과 ──
    yield {
        "stage": "post",
        "reads_writes": reads_writes,
        "fk_edges": fk_edges,
        "column_refs": column_refs,
        "function_table_edges": function_table_edges,
        "module_table_edges": module_table_edges,
        "artifact_table_edges": artifact_table_edges,
    }
