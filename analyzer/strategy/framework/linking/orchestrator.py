# ---------------------------------------------------------------------------
# Step 5 오케스트레이터: run_step5, derive_method_table_edges
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
    MethodFacts,
    FieldFacts,
    SqlStatementChunk,
    ConfigSectionChunk,
    ArtifactChunk,
    ReferToEdge,
    ReadsWritesEdge,
    FkEdge,
    ColumnRef,
    MethodTableEdge,
    ClassTableEdge,
    ArtifactTableEdge,
    UnresolvedLink,
)
from analyzer.strategy.framework.common.utils import sql_op_to_method_relation as _sql_op_to_method_relation
from analyzer.strategy.framework.linking.matcher import (
    link_chunks_to_indexes,
    resolve_candidates,
    identify_entity_classes,
    _infer_table_name_from_entity_fqn,
)
from analyzer.strategy.framework.linking.call_graph import run_call_batch_pipeline

logger = logging.getLogger(__name__)


def derive_method_table_edges(
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    artifact_table_edges: Optional[List[ArtifactTableEdge]] = None,
) -> List[MethodTableEdge]:
    """Method→SqlStatement/ArtifactChunk + reads_writes/artifact_table 체인으로 Method→Table(READS/WRITES) 자동 유도.

    1) Method─REFER_TO→SqlStatement + SqlStatement→Table(reads_writes) → Method─READS/WRITES→Table
    2) Method─REFER_TO→ArtifactChunk + ArtifactChunk→Table(artifact_table_edges) → Method─READS/WRITES→Table
    """
    result: List[MethodTableEdge] = []
    seen: set = set()

    # 1) SQL 경로: SqlStatement → Table
    sql_to_tables: Dict[str, List[Tuple[str, str, str]]] = {}
    for rw in reads_writes:
        if rw.sql_key not in sql_to_tables:
            sql_to_tables[rw.sql_key] = []
        sql_to_tables[rw.sql_key].append((rw.table_schema, rw.table_name, rw.relation))
    for re_ in refer_to_edges:
        if re_.target_type != "SqlStatement":
            continue
        field_id = getattr(re_, "field_id", None)
        for schema, table, sql_rel in sql_to_tables.get(re_.target_id, []):
            method_rel = _sql_op_to_method_relation(sql_rel)
            dedup_key = (re_.method_id, field_id or "", schema, table, method_rel)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(MethodTableEdge(
                method_id=re_.method_id, table_schema=schema,
                table_name=table, relation=method_rel, field_id=field_id,
            ))

    # 2) Artifact 경로: ArtifactChunk → Table
    if artifact_table_edges:
        chunk_to_tables: Dict[str, List[Tuple[str, str]]] = {}
        for at in artifact_table_edges:
            cid = (at.chunk_id or "").strip()
            if cid:
                chunk_to_tables.setdefault(cid, []).append((at.table_schema, at.table_name))
        for re_ in refer_to_edges:
            if re_.target_type != "ArtifactChunk":
                continue
            field_id = getattr(re_, "field_id", None)
            for schema, table in chunk_to_tables.get(re_.target_id, []):
                # ArtifactChunk 경로는 기본 READS (CRUD 세분화는 deepagent confidence에 의존)
                method_rel = "READS"
                dedup_key = (re_.method_id, field_id or "", schema, table, method_rel)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                result.append(MethodTableEdge(
                    method_id=re_.method_id, table_schema=schema,
                    table_name=table, relation=method_rel, field_id=field_id,
                ))

    return result


def run_step5(
    method_facts_list: List[MethodFacts],
    sql_index: Dict[str, SqlStatementChunk],
    sql_chunks: List[SqlStatementChunk],
    config_index: Optional[Dict[str, ConfigSectionChunk]] = None,
    ref_candidates_by_method: Optional[Dict[str, List[Any]]] = None,
    symbol_type_index: Optional[Dict[str, Dict[str, str]]] = None,
    class_index: Optional[Dict[str, Any]] = None,
    field_chunks: Optional[List[Dict[str, Any]]] = None,
    data_dir: Optional[str] = None,
    field_facts_list: Optional[List[FieldFacts]] = None,
    method_index_by_class: Optional[Dict[str, Dict[str, List[str]]]] = None,
    run_dir: Optional[str] = None,
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
    artifact_chunks: Optional[List[ArtifactChunk]] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Step 5: 링킹 — 단계별로 결과를 yield.

    Yields:
        stage="deterministic": refer_to_edges, unresolved
        stage="table_llm": reads_writes, fk_edges, column_refs, table_descriptions
        stage="call_llm": calls_edges, unresolved_calls
        stage="post": reads_writes(final), method_table_edges, class_table_edges, artifact_table_edges
    """
    config_count = len(config_index) if config_index else 0
    logger.info(
        "[FW:STEP5] 링킹 진입  method_facts=%d sql_index=%d sql_chunks=%d config_index=%d field_chunks=%s",
        len(method_facts_list),
        len(sql_index),
        len(sql_chunks),
        config_count,
        len(field_chunks) if field_chunks else 0,
    )
    logger.info("[FW:STEP5] [매칭] 결정론 매칭 (키 in 코드, sql/config/class/artifact 인덱스)")
    refer_to_edges, unresolved = link_chunks_to_indexes(
        method_facts_list,
        field_chunks=field_chunks,
        sql_index=sql_index,
        config_index=config_index,
        class_index=class_index,
        artifact_index=artifact_index,
    )
    det_matched = len(refer_to_edges)
    logger.info(
        "[FW:STEP5] link_chunks_to_indexes 완료  refer_to=%d  미연결=%d",
        det_matched,
        len(unresolved),
    )

    # ref_candidates_by_method 기반 결정론 해석 (CALL_REF / NEW_INSTANCE_REF)
    if ref_candidates_by_method:
        method_index: Dict[str, MethodFacts] = {m.method_id: m for m in method_facts_list}
        rc_matched = 0
        rc_unresolved = 0
        for mid, cands in ref_candidates_by_method.items():
            mf = method_index.get(mid)
            caller_fqn = getattr(mf, "fqn", "") if mf else mid
            code_ref = getattr(mf, "code_ref", None) if mf else None
            add_edges, add_unresolved = resolve_candidates(
                mid,
                caller_fqn,
                code_ref,
                cands,
                sql_index=sql_index,
                config_index=config_index,
                class_index=class_index,
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

    # XML entity에서 이미 처리된 클래스 FQN 수집 → 중복 방지
    xml_entity_class_set: set = set()
    for ac in (artifact_chunks or []):
        ct = getattr(ac, "chunk_type", "")
        if ct in ("entity", "cmp_mapping"):
            for fqn_val in (getattr(ac, "related_class_fqns", None) or []):
                xml_entity_class_set.add(fqn_val)
                xml_entity_class_set.add(fqn_val.rsplit(".", 1)[-1])  # 단순 클래스명도

    entity_fqns = identify_entity_classes(class_index or {})
    for fqn in entity_fqns:
        # XML에서 이미 처리된 클래스면 TABLE 중복 생성 방지
        if fqn in xml_entity_class_set or fqn.rsplit(".", 1)[-1] in xml_entity_class_set:
            logger.debug("[FW:STEP5] 엔티티 스킵 (XML에서 이미 처리) | fqn=%s", fqn)
            continue
        info = (class_index or {}).get(fqn)
        if not isinstance(info, dict):
            continue
        class_key = info.get("class_key") or fqn
        fields = [f for f in (field_facts_list or []) if getattr(f, "class_key", "") == class_key]
        if not fields:
            continue
        field_text = "\n".join(
            f"{f.name} ({getattr(f, 'field_type', '?')})"
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
                method_facts_list,
                field_facts_list or [],
                symbol_type_index or {},
                method_index_by_class or {},
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

    # ── 후처리: entity fallback + edge 유도 ──
    reads_writes = list(reads_writes_list)
    entity_fqns_with_rw = {
        (getattr(rw, "sql_key", "") or "").split("::", 1)[-1].strip()
        for rw in reads_writes
        if (getattr(rw, "sql_key", "") or "").startswith("entity::")
    }
    for fqn in entity_fqns:
        if fqn in entity_fqns_with_rw:
            continue
        table_name = _infer_table_name_from_entity_fqn(fqn)
        if table_name:
            reads_writes.append(
                ReadsWritesEdge(
                    sql_key=f"entity::{fqn}",
                    table_schema="",
                    table_name=table_name,
                    relation="SELECT",
                )
            )
            entity_fqns_with_rw.add(fqn)
    if len(reads_writes) > len(reads_writes_list):
        logger.info(
            "[FW:STEP5] entity:: reads_writes 보정 | 추가=%d (자바 클래스명→테이블명 추론)",
            len(reads_writes) - len(reads_writes_list),
        )
    fk_edges = fk_edges_list
    column_refs = column_refs_list

    # entity::FQN → (CLASS)-[:REFER_TO]->(TABLE)
    seen_ct: Set[Tuple[str, str, str]] = set()
    class_table_edges: List[ClassTableEdge] = []
    for rw in reads_writes:
        sql_key = (getattr(rw, "sql_key", "") or "").strip()
        if not sql_key.startswith("entity::"):
            continue
        class_fqn = sql_key.split("::", 1)[-1].strip()
        if not class_fqn:
            continue
        schema = (getattr(rw, "table_schema", "") or "").strip()
        name = (getattr(rw, "table_name", "") or "").strip()
        if not name:
            continue
        key = (class_fqn, schema, name)
        if key in seen_ct:
            continue
        seen_ct.add(key)
        class_table_edges.append(ClassTableEdge(class_fqn=class_fqn, table_schema=schema, table_name=name))

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

    # Method→Table 자동 유도
    logger.info("[FW:STEP5] [Method–Table] REFER_TO + READS/WRITES 엣지 유도")
    method_table_edges = derive_method_table_edges(refer_to_edges, reads_writes, artifact_table_edges=artifact_table_edges)

    logger.info(
        "[FW:STEP5] 링킹 완료  REFER_TO=%d  reads_writes=%d  Method→Table=%d  Class→Table=%d  Artifact→Table=%d  미연결=%d",
        len(refer_to_edges),
        len(reads_writes),
        len(method_table_edges),
        len(class_table_edges),
        len(artifact_table_edges),
        len(unresolved),
    )

    # ── 4단계 yield: 후처리 결과 ──
    yield {
        "stage": "post",
        "reads_writes": reads_writes,
        "fk_edges": fk_edges,
        "column_refs": column_refs,
        "method_table_edges": method_table_edges,
        "class_table_edges": class_table_edges,
        "artifact_table_edges": artifact_table_edges,
    }
