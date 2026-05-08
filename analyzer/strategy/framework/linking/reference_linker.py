"""참조 링킹 — SQL/Config/Artifact 키 매칭 + 후보 해석 + 엣지 derive.

본 모듈은 Reference 도메인 비즈니스 로직 (단일 처리 흐름):
  1. 키 매칭: 메서드/필드 코드에서 인덱스 키(SQL id, config key 등)를 문자열 매칭으로
     찾아 REFER_TO 엣지 생성
  2. 후보 해석: AST 추출 ref 후보를 결정론적으로 해석
  3. 엣지 derive: REFER_TO + reads_writes/artifact_table 체인으로 Function→Table 자동 유도
  4. 엔티티 청크 합류: DDL 없을 때 외부파일·어노테이션 entity → SQL LLM 입력으로 합류
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from analyzer.strategy.framework.common.models.node_models import (
    ArtifactChunk,
    ArtifactTableEdge,
    ConfigSectionChunk,
    FunctionFacts,
    FunctionTableEdge,
    ModuleTableEdge,
    ReadsWritesEdge,
    ReferenceCandidate,
    ReferToEdge,
    SqlStatementChunk,
    UnresolvedLink,
    VariableFacts,
)
from analyzer.strategy.framework.linking.entity_linker import identify_entity_classes
from analyzer.strategy.framework.linking.table_linker import sql_op_to_method_relation

logger = logging.getLogger(__name__)


def key_in_code(code: str, key: str) -> bool:
    """인덱스 키가 코드에 따옴표로 감싸져 등장하는지 검사.

    인덱스 키(SQL id, config key 등)는 코드에서 항상 문자열 리터럴로 사용되므로
    따옴표 매칭만 수행한다. 단어 경계 매칭은 변수명/함수명과 우연히 겹칠 위험이 있어 사용하지 않는다.
    """
    if not code or not key:
        return False
    return ('"' + key + '"') in code or ("'" + key + "'") in code


def link_chunks_to_indexes(
    function_facts_list: List[FunctionFacts],
    sql_index: Optional[Dict[str, SqlStatementChunk]] = None,
    config_index: Optional[Dict[str, ConfigSectionChunk]] = None,
    module_index: Optional[Dict[str, Any]] = None,
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
) -> Tuple[List[ReferToEdge], List[UnresolvedLink]]:
    """Method/Field 청크 코드에 인덱스 키가 있으면 REFER_TO 연결. 문자열 추출/패턴 없이 key in code 만 검사."""
    edges: List[ReferToEdge] = []
    unresolved: List[UnresolvedLink] = []
    seen: set = set()  # (source_id, source_kind, target_type, target_id)

    artifact_key_index: Dict[str, List[str]] = {}
    if artifact_index:
        for chunk_id, ac in artifact_index.items():
            keys: List[str] = []
            key_id = (chunk_id or "").strip()
            if key_id:
                keys.append(key_id)
            hint = ac.hint_key
            hint = (hint or "").strip()
            if hint:
                keys.append(hint)
            for k in keys:
                artifact_key_index.setdefault(k, []).append(key_id)

    def add_edge(function_id: str, variable_id: Optional[str], target_type: str, target_id: str, evidence: Dict[str, Any]) -> None:
        sid = variable_id if variable_id else function_id
        kind = "Variable" if variable_id else "Function"
        t = (sid, kind, target_type, target_id)
        if t in seen:
            return
        seen.add(t)
        edges.append(
            ReferToEdge(
                function_id=function_id,
                target_type=target_type,
                target_id=target_id,
                evidence=evidence,
                confidence=1.0,
                variable_id=variable_id,
            )
        )

    # 매칭된 인덱스 키 추적 (매칭 실패 키 → unresolved → deepagent)
    matched_keys: Set[Tuple[str, str]] = set()  # (target_type, key)

    for m in function_facts_list:
        code = m.code.strip()
        if not code:
            continue
        evidence = {"file": m.code_ref, "line_range": [m.start_line, m.end_line]}
        if sql_index:
            for key in sql_index:
                if key_in_code(code, key):
                    add_edge(m.function_id, None, "SqlStatement", key, evidence)
                    matched_keys.add(("SqlStatement", key))
        if config_index:
            for key in config_index.keys():
                if key_in_code(code, key):
                    add_edge(m.function_id, None, "ConfigSection", key, evidence)
                    matched_keys.add(("ConfigSection", key))
        if artifact_key_index:
            for key, chunk_ids in artifact_key_index.items():
                if not key_in_code(code, key):
                    continue
                for cid in chunk_ids:
                    if cid:
                        add_edge(m.function_id, None, "ArtifactChunk", cid, evidence)
                        matched_keys.add(("ArtifactChunk", key))

    # 매칭 실패 인덱스 키 수집 → unresolved (deepagent 대상)
    # sql, config, artifact 인덱스 키 중 어떤 메서드/필드에도 매칭되지 않은 것
    if sql_index:
        for key, stmt in sql_index.items():
            if ("SqlStatement", key) not in matched_keys:
                unresolved.append(UnresolvedLink(
                    function_id="", caller_fqn="",
                    ref_candidates=[{
                        "target_type": "SqlStatement",
                        "key": key,
                        "category": "sql",
                        "sql_type": stmt.sql_type,
                        "file_path": stmt.file_path,
                    }],
                ))
    if config_index:
        for key, cfg in config_index.items():
            if ("ConfigSection", key) not in matched_keys:
                unresolved.append(UnresolvedLink(
                    function_id="", caller_fqn="",
                    ref_candidates=[{
                        "target_type": "ConfigSection",
                        "key": key,
                        "category": "config",
                        "file_path": cfg.file_path,
                    }],
                ))
    if artifact_index:
        for chunk_id, ac in artifact_index.items():
            hint = (ac.hint_key or "").strip()
            chunk_id_clean = (chunk_id or "").strip()
            if ("ArtifactChunk", chunk_id_clean) in matched_keys:
                continue
            if hint and ("ArtifactChunk", hint) in matched_keys:
                continue
            chunk_type = ac.chunk_type
            category = chunk_type if chunk_type else "artifact"
            # 원문 전달 (토큰 한도 내에서, 초과 시 잘라서)
            full_text = (ac.text or "").strip()
            _MAX_SNIPPET_CHARS = 6000  # ~2000 토큰 상당
            snippet = full_text if len(full_text) <= _MAX_SNIPPET_CHARS else full_text[:_MAX_SNIPPET_CHARS]
            unresolved.append(UnresolvedLink(
                function_id="", caller_fqn="",
                ref_candidates=[{
                    "target_type": "ArtifactChunk",
                    "key": hint or chunk_id_clean,
                    "chunk_id": chunk_id_clean,
                    "category": category,
                    "chunk_type": chunk_type,
                    "text_snippet": snippet,
                    "file_path": ac.source_path,
                }],
            ))

    logger.info(
        "[PHASE:2:LINKING] link_chunks_to_indexes 완료 | edges=%d unmatched_keys=%d",
        len(edges), len(unresolved),
    )
    return edges, unresolved


def resolve_candidates(
    function_id: str,
    caller_fqn: str,
    code_ref: Optional[str],
    candidates: List[Any],
    sql_index: Optional[Dict[str, SqlStatementChunk]],
    config_index: Optional[Dict[str, ConfigSectionChunk]],
    module_index: Optional[Dict[str, Any]],
    symbol_type_index: Optional[Dict[str, Dict[str, str]]],
    artifact_index: Optional[Dict[str, ArtifactChunk]] = None,
) -> Tuple[List[ReferToEdge], List[UnresolvedLink]]:
    """AST에서 추출된 ref 후보를 결정론적으로 해석해 ReferToEdge 또는 UnresolvedLink 로 분류."""
    edges: List[ReferToEdge] = []
    unresolved: List[UnresolvedLink] = []
    sym_idx = symbol_type_index or {}
    module_idx = module_index or {}
    art_idx = artifact_index or {}

    def _as_dict(c: Any) -> Dict[str, Any]:
        if isinstance(c, dict):
            return c
        if isinstance(c, ReferenceCandidate):
            return {
                "kind": c.kind,
                "value": c.value,
                "receiver": c.receiver,
                "function_name": c.function_name,
                "moduleName": c.moduleName,
                "path": c.path,
                "line_range": c.line_range,
                "code_snippet": c.code_snippet,
            }
        return {"kind": getattr(c, "kind", ""), "value": getattr(c, "value", None)}

    for raw in candidates or []:
        c = _as_dict(raw)
        kind = c.get("kind", "") or ""
        value = c.get("value")
        target_type: Optional[str] = None
        target_id: Optional[str] = None

        # SQL / Config 키 직접 참조
        if kind == "SQL_KEY_LITERAL" and value and sql_index:
            if value in sql_index:
                target_type, target_id = "SqlStatement", value
            else:
                unresolved.append(
                    UnresolvedLink(function_id=function_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range"))
                )
                continue
        elif kind == "SQL_LITERAL":
            unresolved.append(
                UnresolvedLink(function_id=function_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range"))
            )
            continue
        elif kind == "CONFIG_KEY_REF" and value and config_index:
            if value in config_index:
                target_type, target_id = "ConfigSection", value
            else:
                unresolved.append(
                    UnresolvedLink(function_id=function_id, caller_fqn=caller_fqn, ref_candidates=[c], file=code_ref, line_range=c.get("line_range"))
                )
                continue

        # 호출/인스턴스 → Class 결정 (실패 시 skip, deepagent 대상 아님)
        elif kind == "CALL_REF":
            receiver = c.get("receiver")
            receiver_type = (sym_idx.get(function_id) or {}).get(receiver) if receiver else None
            if receiver_type:
                info = module_idx.get(receiver_type)
                if isinstance(info, dict):
                    target_type, target_id = "Module", info.get("fqn") or receiver_type
                elif info:
                    target_type, target_id = "Module", str(info)
            if not target_type:
                continue  # 클래스 매핑 실패 → skip (deepagent 대상 아님)
        elif kind == "NEW_INSTANCE_REF":
            module_name = c.get("moduleName")
            if module_name:
                info = module_idx.get(module_name)
                if isinstance(info, dict):
                    target_type, target_id = "Module", info.get("fqn", module_name)
                elif module_name in module_idx:
                    target_type, target_id = "Module", module_name
            if not target_type:
                continue  # 클래스 매핑 실패 → skip (deepagent 대상 아님)

        else:
            continue  # 알 수 없는 kind → skip

        if target_type and target_id:
            edges.append(
                ReferToEdge(
                    function_id=function_id,
                    target_type=target_type,
                    target_id=target_id,
                    evidence={"file": code_ref, "candidate_kind": kind},
                    confidence=1.0,
                    variable_id=None,
                )
            )

    return edges, unresolved


# ═══════════════════════════════════════════════════════════════════════════
# 후처리: REFER_TO + reads_writes 체인으로 Function→Table 자동 유도
# ═══════════════════════════════════════════════════════════════════════════


def derive_function_table_edges(
    refer_to_edges: List[ReferToEdge],
    reads_writes: List[ReadsWritesEdge],
    artifact_table_edges: Optional[List[ArtifactTableEdge]] = None,
) -> List[FunctionTableEdge]:
    """Function→SqlStatement/ArtifactChunk + reads_writes/artifact_table 체인으로 Function→Table 자동 유도.

    1) Function─REFER_TO→SqlStatement + SqlStatement→Table(reads_writes) → Function─READS/WRITES→Table
    2) Function─REFER_TO→ArtifactChunk + ArtifactChunk→Table(artifact_table_edges) → Function─READS/WRITES→Table
    """
    result: List[FunctionTableEdge] = []
    seen: set = set()

    # 경로 1) SQL: SqlStatement → Table
    sql_to_tables: Dict[str, List[Tuple[str, str, str]]] = {}
    for rw in reads_writes:
        sql_to_tables.setdefault(rw.sql_key, []).append((rw.table_schema, rw.table_name, rw.relation))

    for edge in refer_to_edges:
        if edge.target_type != "SqlStatement":
            continue
        variable_id = getattr(edge, "variable_id", None)
        for schema, table, sql_rel in sql_to_tables.get(edge.target_id, []):
            fn_rel = sql_op_to_method_relation(sql_rel)
            op = (sql_rel or "").strip().upper()
            dedup_key = (edge.function_id, variable_id or "", schema, table, fn_rel, op)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(FunctionTableEdge(
                function_id=edge.function_id, table_schema=schema,
                table_name=table, relation=fn_rel, variable_id=variable_id, op=op,
            ))

    # 경로 2) Artifact: ArtifactChunk → Table
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


# ═══════════════════════════════════════════════════════════════════════════
# 엔티티 청크 합류 — DDL 없을 때 외부파일·어노테이션 entity → SQL LLM 입력
# ═══════════════════════════════════════════════════════════════════════════


def augment_table_chunks_with_entities(
    sql_chunks: List[SqlStatementChunk],
    module_index: Optional[Dict[str, Any]],
    variable_facts_list: Optional[List[VariableFacts]],
    artifact_index: Optional[Dict[str, ArtifactChunk]],
    artifact_chunks: Optional[List[ArtifactChunk]],
    ddl_table_metadata: Optional[Dict[str, Any]],
) -> List[SqlStatementChunk]:
    """DDL 없을 때 외부파일(XML entity, 어노테이션) → entity_definition 청크로 합류."""
    combined: List[SqlStatementChunk] = list(sql_chunks or [])
    has_entity_info = bool(identify_entity_classes(module_index or {})) or any(
        ac.chunk_type in ("entity", "cmp_mapping")
        for ac in (artifact_index or {}).values()
    )

    if has_entity_info and not ddl_table_metadata:
        entity_fqns = identify_entity_classes(module_index or {})
        for fqn in entity_fqns:
            info = (module_index or {}).get(fqn)
            if not isinstance(info, dict):
                continue
            module_key = info.get("module_key") or fqn
            fields = [f for f in (variable_facts_list or []) if f.module_key == module_key]
            if not fields:
                continue
            field_text = "\n".join(
                f"{f.name} ({(f.variable_type or '?')})"
                + (f" {f.annotations}" if f.annotations else "")
                for f in fields
            )
            combined.append(SqlStatementChunk(
                sql_key=f"entity::{fqn}", sql_type="entity_definition",
                sql_text=field_text, start_line=0, end_line=0, file_path="",
            ))
        for ac in (artifact_chunks or []):
            if ac.chunk_type not in ("entity", "cmp_mapping"):
                continue
            text = (ac.text or "").strip()
            if not text:
                continue
            combined.append(SqlStatementChunk(
                sql_key=f"entity_xml::{ac.chunk_id}",
                sql_type="entity_definition",
                sql_text=text,
                start_line=ac.start_line,
                end_line=ac.end_line,
                file_path=ac.source_path,
            ))
        entity_count = len(combined) - len(sql_chunks or [])
        if entity_count:
            logger.info(
                "[PHASE:2:LINKING] 엔티티 청크 %d개를 table LLM 입력에 합류 (sql=%d + entity=%d)",
                entity_count, len(sql_chunks or []), entity_count,
            )
    elif ddl_table_metadata:
        logger.info("[PHASE:2:LINKING] DDL 테이블 메타데이터 존재 — 엔티티 청크 생성 스킵")
    elif not has_entity_info:
        logger.info("[PHASE:2:LINKING] 외부 엔티티 정보 없음 — DeepAgent 엔티티 식별 대상")

    return combined


def derive_module_table_edges_from_entity_keys(
    reads_writes: List[ReadsWritesEdge],
) -> List[ModuleTableEdge]:
    """entity::FQN sql_key → ModuleTableEdge."""
    seen: Set[Tuple[str, str, str]] = set()
    out: List[ModuleTableEdge] = []
    for rw in reads_writes:
        sql_key = (rw.sql_key or "").strip()
        if not sql_key.startswith("entity::"):
            continue
        module_fqn = sql_key.split("::", 1)[-1].strip()
        if not module_fqn:
            continue
        schema = (rw.table_schema or "").strip()
        name = (rw.table_name or "").strip()
        if not name:
            continue
        key = (module_fqn, schema, name)
        if key in seen:
            continue
        seen.add(key)
        out.append(ModuleTableEdge(module_fqn=module_fqn, table_schema=schema, table_name=name))
    return out


def derive_artifact_table_edges_from_entity_keys(
    reads_writes: List[ReadsWritesEdge],
) -> List[ArtifactTableEdge]:
    """entity_xml::chunk_id sql_key → ArtifactTableEdge."""
    seen: Set[Tuple[str, str, str]] = set()
    out: List[ArtifactTableEdge] = []
    for rw in reads_writes:
        sql_key = (rw.sql_key or "").strip()
        if not sql_key.startswith("entity_xml::"):
            continue
        chunk_id = sql_key.split("::", 1)[-1].strip()
        if not chunk_id:
            continue
        schema = (rw.table_schema or "").strip()
        name = (rw.table_name or "").strip()
        if not name:
            continue
        key = (chunk_id, schema, name)
        if key in seen:
            continue
        seen.add(key)
        out.append(ArtifactTableEdge(chunk_id=chunk_id, table_schema=schema, table_name=name))
    return out
