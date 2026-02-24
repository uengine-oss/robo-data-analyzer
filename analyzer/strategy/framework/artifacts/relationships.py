"""Relationships - Identify explicit and implicit relations between entities.

관계 타입은 Framework 스키마에 맞춰 변환됩니다:
- DEPENDS_ON → DEPENDENCY
- CALLS_METHOD → CALLS
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, AsyncGenerator

from config.settings import settings
from util.stream_event import emit_data, emit_message, emit_phase_event, emit_phase_result
from util.text_utils import log_process

from analyzer.strategy.framework.artifacts.data_models import (
    Chunk,
    EdgeType,
    FileCategory,
    FileInfo,
    PatternRule,
    PhaseResult,
    PhaseStatus,
    ProjectStructure,
    Relationship,
)


def _map_edge_type_to_framework(edge_type: EdgeType) -> str:
    """Map deep-agents edge type to Framework schema."""
    mapping = {
        EdgeType.DEPENDS_ON: "DEPENDENCY",  # Framework와 통일
        EdgeType.CALLS_METHOD: "CALLS",  # Framework와 통일
        EdgeType.IMPORTS: "IMPORTS",
        EdgeType.EXTENDS: "EXTENDS",
        EdgeType.IMPLEMENTS: "IMPLEMENTS",
        EdgeType.CONTAINS: "CONTAINS",
        EdgeType.HAS_METHOD: "HAS_METHOD",
        EdgeType.MAPS_TO_SQL: "MAPS_TO_SQL",
        EdgeType.MAPS_TO_TABLE: "MAPS_TO_TABLE",
        EdgeType.CALLS_SQL: "CALLS_SQL",
        EdgeType.REFERENCES_BEAN: "REFERENCES_BEAN",
        EdgeType.CONFIGURES: "CONFIGURES",
        EdgeType.MATCHES_BY_NAME: "MATCHES_BY_NAME",
        EdgeType.USES: "USES",
        EdgeType.SELECT: "SELECT",
        EdgeType.INSERT: "INSERT",
        EdgeType.UPDATE: "UPDATE",
        EdgeType.DELETE: "DELETE",
    }
    return mapping.get(edge_type, edge_type.value)


async def run_relationships(
    project: ProjectStructure,
    chunks: list[Chunk],
    generated_codes: dict | None = None,
    ast_context: list[Any] | None = None,
    relationships_out: list[Relationship] | None = None,
    class_index_out: dict[str, dict[str, Any]] | None = None,
    bean_index_out: dict[str, dict[str, Any]] | None = None,
    sql_index_out: dict[str, dict[str, Any]] | None = None,
    table_index_out: dict[str, dict[str, Any]] | None = None,
) -> AsyncGenerator[bytes, None]:
    """관계 발견

    Args:
        project: Phase 1.5에서 생성된 프로젝트 구조
        chunks: Phase 3에서 생성된 청크
        generated_codes: LLM 생성 코드 (현재는 미사용)
        ast_context: Java AST 노드 정보 리스트 (class_index 및 external_refs용). 각 항목은 node_id, fqn, name, package_name, external_refs 등 포함.

    Yields:
        NDJSON 스트리밍 데이터
    """
    # Phase 4 시작 이벤트
    yield emit_phase_event(4, "비타겟 관계 발견", "started", 0, {"canPause": True})

    try:
        all_relationships: list[Relationship] = []
        pattern_rules: list[PatternRule] = []

        # Build indexes for efficient lookup
        log_process("ANALYZE", "PHASE_4", "인덱스 구축 중...")
        class_index = _build_class_index_from_ast(ast_context) if ast_context else _build_class_index(chunks)
        file_index = _build_file_index(project.files)
        bean_index = _build_bean_index(chunks)
        sql_index = _build_sql_index(chunks)
        table_index = _build_table_index(chunks)

        log_process(
            "ANALYZE", "PHASE_4",
            f"인덱스 구축 완료: {len(class_index)}개 클래스, {len(bean_index)}개 빈, "
            f"{len(sql_index)}개 SQL, {len(table_index)}개 테이블, {len(file_index)}개 파일"
        )

        # 4-A: Explicit relations (chunk-based: SQL→Class, Bean→Class 등)
        log_process("ANALYZE", "PHASE_4", "명시적 관계 발견 중...")
        explicit = await _discover_explicit_relations(chunks, class_index, bean_index, sql_index)
        all_relationships.extend(explicit)
        log_process("ANALYZE", "PHASE_4", f"명시적 관계 {len(explicit)}개 발견")

        # 4-A-2: Java → 외부 리소스 (externalRefs, kind 기반 디스패치)
        if ast_context and class_index:
            external_rel = _discover_external_refs_relations(ast_context, sql_index, table_index)
            all_relationships.extend(external_rel)
            if external_rel:
                log_process("ANALYZE", "PHASE_4", f"externalRefs 기반 관계 {len(external_rel)}개 발견")
        
        # 4-B: Implicit relations
        log_process("ANALYZE", "PHASE_4", "암시적 관계 발견 중...")
        implicit = await _discover_implicit_relations(project, chunks, class_index, file_index)
        all_relationships.extend(implicit)
        log_process("ANALYZE", "PHASE_4", f"암시적 관계 {len(implicit)}개 발견")
        
        # 4-C: Meta-level pattern discovery
        log_process("ANALYZE", "PHASE_4", "메타 레벨 패턴 발견 중...")
        pattern_rules = _discover_meta_patterns(project, all_relationships)
        if pattern_rules:
            log_process("ANALYZE", "PHASE_4", f"{len(pattern_rules)}개 메타 레벨 패턴 발견")
        
        # Apply meta-patterns
        additional = _apply_pattern_rules(project, chunks, pattern_rules, class_index, file_index)
        all_relationships.extend(additional)
        if additional:
            log_process("ANALYZE", "PHASE_4", f"패턴 적용: {len(additional)}개 추가 관계 발견")
        
        # 스트리밍 (관계별)
        for rel in all_relationships[:100]:  # 최대 100개만 스트리밍
            edge_type_str = _map_edge_type_to_framework(rel.edge_type)
            yield emit_message(f"   🔗 {rel.source_id} -[{edge_type_str}]-> {rel.target_id}")
        
        log_process("ANALYZE", "PHASE_4", f"총 관계 {len(all_relationships)}개 발견")
        
        # Phase 4 완료 이벤트
        yield emit_phase_event(4, "비타겟 관계 발견", "completed", 100, {
            "total_relationships": len(all_relationships),
            "explicit": len(explicit),
            "implicit": len(implicit),
            "pattern_based": len(additional),
        })
        
        # Phase 결과 반환 (요약 정보만 전송)
        yield emit_phase_result(4, {
            "total_relationships": len(all_relationships),
            "explicit": len(explicit),
            "implicit": len(implicit),
            "pattern_based": len(additional),
        })
        
        # 호출 측에 실제 객체 전달
        if relationships_out is not None:
            relationships_out.clear()
            relationships_out.extend(all_relationships)
        if class_index_out is not None:
            class_index_out.clear()
            class_index_out.update(class_index)
        if bean_index_out is not None:
            bean_index_out.clear()
            bean_index_out.update(bean_index)
        if sql_index_out is not None:
            sql_index_out.clear()
            sql_index_out.update(sql_index)
        if table_index_out is not None:
            table_index_out.clear()
            table_index_out.update(table_index)
        
    except Exception as e:
        log_process("ANALYZE", "ERROR", f"Phase 4 실패: {e}", exc_info=True)
        yield emit_phase_event(4, "비타겟 관계 발견", "failed", 0, {"error": str(e)})
        raise


def _build_class_index_from_ast(ast_context: list[Any]) -> dict[str, dict[str, Any]]:
    """Build class index from Java AST context (node_id, fqn, name, package_name)."""
    index: dict[str, dict[str, Any]] = {}
    for item in ast_context:
        if not isinstance(item, dict):
            continue
        node_type = item.get("node_type", "")
        if node_type not in ("CLASS", "INTERFACE", "ENUM"):
            continue
        node_id = item.get("node_id") or item.get("chunk_id")
        fqn = item.get("fqn", "")
        name = item.get("name") or item.get("unit_name") or (fqn.split(".")[-1] if fqn else "")
        package = item.get("package_name") or item.get("package", "")
        if not node_id:
            continue
        entry = {
            "chunk_id": node_id,
            "name": name,
            "fqn": fqn,
            "package": package,
            "file": item.get("file", ""),
            "imports": item.get("imports", []),
        }
        if fqn:
            index[fqn] = entry
        if name and name != fqn:
            index[name] = entry
    return index


def _build_class_index(chunks: list[Chunk]) -> dict[str, dict[str, Any]]:
    """Build an index of classes (from artifacts chunks, if any)."""
    index: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        if chunk.metadata.chunk_type == "class":
            props = chunk.metadata.properties
            fqn = props.get("fqn", props.get("name", ""))
            name = props.get("name", "")
            if fqn:
                index[fqn] = {
                    "chunk_id": chunk.chunk_id,
                    "name": name,
                    "fqn": fqn,
                    "file": chunk.metadata.source_file,
                    "imports": props.get("imports", []),
                    "package": props.get("package", ""),
                }
                if name and name != fqn:
                    index[name] = index[fqn]
    return index


def _build_table_index(chunks: list[Chunk]) -> dict[str, dict[str, Any]]:
    """Build table index from SQL statement chunks (table name -> node_id for Phase 5 Table node)."""
    from util.sql_analyzer import extract_tables_from_sql

    index: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        if chunk.metadata.chunk_type != "sql_statement":
            continue
        sql_text = chunk.content or ""
        tables = extract_tables_from_sql(sql_text)
        for tbl in tables:
            schema = tbl.get("schema", "")
            table = tbl.get("table", "")
            full_name = f"{schema}.{table}" if schema else table
            if not full_name:
                continue
            node_id = f"table:{full_name}"
            index[full_name] = {"node_id": node_id, "schema": schema, "table": table}
            # Also index by table name only for JPA-style "USERS"
            if schema and table and table not in index:
                index[table] = {"node_id": node_id, "schema": schema, "table": table}
    return index


def _discover_external_refs_relations(
    ast_context: list[Any],
    sql_index: dict[str, dict[str, Any]],
    table_index: dict[str, dict[str, Any]],
) -> list[Relationship]:
    """Discover Java -> external resource relations from ast_context.external_refs (kind-based dispatch)."""
    relationships: list[Relationship] = []
    for item in ast_context:
        if not isinstance(item, dict):
            continue
        node_id = item.get("node_id") or item.get("chunk_id")
        external_refs = item.get("external_refs") or item.get("externalRefs") or []
        if not node_id or not external_refs:
            continue
        for ref in external_refs:
            if not isinstance(ref, dict):
                continue
            kind = (ref.get("kind") or "").strip().lower()
            target = (ref.get("target") or "").strip()
            if not target:
                continue
            if kind == "mybatis_sql":
                info = sql_index.get(target)
                if info:
                    relationships.append(Relationship(
                        source_id=node_id,
                        target_id=info["chunk_id"],
                        edge_type=EdgeType.MAPS_TO_SQL,
                        confidence=1.0,
                        properties={"discovery_method": "externalRefs:mybatis_sql", "sql_reference": target},
                    ))
            elif kind == "jpa_table":
                info = table_index.get(target) or table_index.get(target.upper())
                if info:
                    relationships.append(Relationship(
                        source_id=node_id,
                        target_id=info["node_id"],
                        edge_type=EdgeType.MAPS_TO_TABLE,
                        confidence=1.0,
                        properties={"discovery_method": "externalRefs:jpa_table", "table": target},
                    ))
            elif kind == "jpa_column":
                # column_index could be added later; for now skip or use table.column -> column node_id
                pass
            elif kind == "config_key":
                # config_index from yaml/properties chunks could be added later
                pass
    return relationships


def _build_file_index(files: list[FileInfo]) -> dict[str, FileInfo]:
    """Build an index of files by relative path and by stem name."""
    index: dict[str, FileInfo] = {}
    for f in files:
        index[f.relative_path] = f
        stem = f.path.stem
        index[stem] = f
    return index


def _build_bean_index(chunks: list[Chunk]) -> dict[str, dict[str, Any]]:
    """Build an index of Spring beans."""
    index: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        if chunk.metadata.chunk_type == "spring_bean":
            props = chunk.metadata.properties
            bean_id = props.get("id", "")
            if bean_id:
                index[bean_id] = {
                    "chunk_id": chunk.chunk_id,
                    "id": bean_id,
                    "class": props.get("class", ""),
                    "file": chunk.metadata.source_file,
                }
    return index


def _build_sql_index(chunks: list[Chunk]) -> dict[str, dict[str, Any]]:
    """Build an index of SQL statements."""
    index: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        if chunk.metadata.chunk_type == "sql_statement":
            props = chunk.metadata.properties
            ns = props.get("namespace", "")
            stmt_id = props.get("id", "")
            key = f"{ns}.{stmt_id}" if ns else stmt_id
            if key:
                index[key] = {
                    "chunk_id": chunk.chunk_id,
                    "namespace": ns,
                    "id": stmt_id,
                    "type": props.get("type", ""),
                    "file": chunk.metadata.source_file,
                }
    return index


async def _discover_explicit_relations(
    chunks: list[Chunk],
    class_index: dict[str, dict[str, Any]],
    bean_index: dict[str, dict[str, Any]],
    sql_index: dict[str, dict[str, Any]],
) -> list[Relationship]:
    """Discover explicit relations from chunk content."""
    relationships: list[Relationship] = []
    
    # SQL → Class (parameterClass, resultClass)
    for chunk in chunks:
        if chunk.metadata.chunk_type == "sql_statement":
            props = chunk.metadata.properties
            param_class = props.get("parameterClass", props.get("parameterType", ""))
            result_class = props.get("resultClass", props.get("resultType", ""))
            
            if param_class and param_class in class_index:
                relationships.append(Relationship(
                    source_id=chunk.chunk_id,
                    target_id=class_index[param_class]["chunk_id"],
                    edge_type=EdgeType.PARAMETER_TYPE,
                    confidence=1.0,
                    properties={"discovery_method": "explicit:sql_parameter"},
                ))
            
            if result_class and result_class in class_index:
                relationships.append(Relationship(
                    source_id=chunk.chunk_id,
                    target_id=class_index[result_class]["chunk_id"],
                    edge_type=EdgeType.RESULT_TYPE,
                    confidence=1.0,
                    properties={"discovery_method": "explicit:sql_result"},
                ))
        
        # Spring Bean → Class
        elif chunk.metadata.chunk_type == "spring_bean":
            props = chunk.metadata.properties
            bean_class = props.get("class", "")
            if bean_class and bean_class in class_index:
                relationships.append(Relationship(
                    source_id=chunk.chunk_id,
                    target_id=class_index[bean_class]["chunk_id"],
                    edge_type=EdgeType.REFERENCES_BEAN,
                    confidence=1.0,
                    properties={"discovery_method": "explicit:bean_class"},
                ))
    
    return relationships


async def _discover_implicit_relations(
    project: ProjectStructure,
    chunks: list[Chunk],
    class_index: dict[str, dict[str, Any]],
    file_index: dict[str, FileInfo],
) -> list[Relationship]:
    """Discover implicit relations using naming conventions and directory mapping."""
    relationships: list[Relationship] = []
    
    # Naming convention matching (e.g., UserDAO → User)
    for chunk in chunks:
        if chunk.metadata.chunk_type == "class":
            props = chunk.metadata.properties
            name = props.get("name", "")
            if name:
                # Try to find related classes by name pattern
                for other_fqn, other_info in class_index.items():
                    if other_fqn == props.get("fqn", ""):
                        continue
                    other_name = other_info.get("name", "")
                    if _matches_naming_convention(name, other_name):
                        relationships.append(Relationship(
                            source_id=chunk.chunk_id,
                            target_id=other_info["chunk_id"],
                            edge_type=EdgeType.DEPENDS_ON,  # Will be mapped to DEPENDENCY
                            confidence=0.7,
                            properties={"discovery_method": "implicit:naming_convention"},
                        ))
    
    return relationships


def _matches_naming_convention(name1: str, name2: str) -> bool:
    """Check if two names match a common naming convention pattern."""
    # Simple patterns: UserDAO → User, UserService → User, etc.
    patterns = [
        (r"^(.+?)(DAO|Service|Controller|Repository)$", r"^\1$"),
        (r"^(.+?)(Impl|Implementation)$", r"^\1$"),
    ]
    
    for pattern, replacement in patterns:
        match = re.match(pattern, name1)
        if match:
            base = match.group(1)
            if base == name2:
                return True
    
    return False


def _discover_meta_patterns(
    project: ProjectStructure,
    relationships: list[Relationship],
) -> list[PatternRule]:
    """Discover meta-level patterns from relationships."""
    # TODO: Implement pattern discovery
    return []


def _apply_pattern_rules(
    project: ProjectStructure,
    chunks: list[Chunk],
    pattern_rules: list[PatternRule],
    class_index: dict[str, dict[str, Any]],
    file_index: dict[str, FileInfo],
) -> list[Relationship]:
    """Apply pattern rules to discover additional relationships."""
    # TODO: Implement pattern application
    return []

