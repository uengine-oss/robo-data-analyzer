"""Framework Neo4j Cypher 쿼리 빌더.

42개 쿼리 빌더 함수를 아래 섹션으로 구분한다:
  1. Helpers           — FQN 파싱, 이름 추출 등 순수 유틸
  2. Node MERGE        — 개별 노드 생성 (FUNCTION, MODULE, TABLE, Column 등)
  3. Relationship MERGE — 구조·FK·호출·참조 관계 생성
  4. Summary / Label   — summary·stereotype 속성 업데이트 및 라벨 추가
  5. CALLS / USES      — 호출·의존 관계 (UNWIND 배치)
  6. UML               — 상속·연관·의존·합성·집합 관계
  7. UNWIND 배치       — 대량 노드용 벌크 MERGE
  8. Table Description — 메서드 summary 기반 테이블 설명 보강
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from analyzer.strategy.framework.graph.core import (
    _merge_node_query,
    _merge_rel_query,
    _build_unwind_query,
)
from analyzer.strategy.framework.common.models import (
    ArtifactChunk,
    ArtifactTableEdge,
    CallsEdge,
    ChunkBundle,
    ModuleTableEdge,
    ConfigSectionChunk,
    ReferToEdge,
    ColumnRef,
    FkEdge,
    VariableFacts,
    FunctionFacts,
    FunctionTableEdge,
    ReadsWritesEdge,
    SqlStatementChunk,
    UsesEdge,
)
from analyzer.strategy.framework.common.constants import (
    TABLE_DB_DEFAULT,
    TABLE_SCHEMA_DEFAULT,
    EXCLUDED_TABLE_NAMES,
    ARTIFACT_CODE_MAX,
    CODE_TEXT_MAX,
    SQL_TEXT_MAX,
)

# 모듈 레벨 상수 (시스템/더미 테이블 제외, 코드 길이 제한)
_EXCLUDED_TABLE_NAMES = EXCLUDED_TABLE_NAMES
_ARTIFACT_CODE_MAX = ARTIFACT_CODE_MAX
_CHUNK_CODE_MAX = CODE_TEXT_MAX
_SQL_TEXT_MAX = SQL_TEXT_MAX


# ═══════════════════════════════════════════════════════════════════════════
# 1. Helpers — FQN 파싱, 이름 추출 등 순수 유틸
# ═══════════════════════════════════════════════════════════════════════════

def _extract_short_name(fqn: Optional[str]) -> str:
    """FQN에서 마지막 이름 추출 (예: 'com.abc.Foo' → 'Foo')."""
    if not fqn:
        return ""
    return fqn.rsplit(".", 1)[-1]


def _split_file_path(path: str) -> tuple[str, str]:
    """경로를 디렉토리와 파일명으로 분리 (/ 및 \\ 지원)."""
    if "/" in path:
        parts = path.rsplit("/", 1)
        return parts[0], parts[1]
    if "\\" in path:
        parts = path.rsplit("\\", 1)
        return parts[0], parts[1]
    return "", path


def _build_column_fqn(schema: str, table: str, column: str) -> str:
    """Column FQN 생성 (schema.table.column)."""
    s = (schema or "").strip() or TABLE_SCHEMA_DEFAULT
    return f"{s}.{table}.{column}"


def _normalize_stereotype(value: Any) -> str:
    """command/readmodel만 허용, 그 외는 readmodel."""
    s = (value or "").strip().lower()
    return s if s in ("command", "readmodel") else "readmodel"


def _module_fqn_and_function_name(fqn: Optional[str]) -> tuple[str, str]:
    """fqn(예: com.abc.Foo.bar) → (module_fqn, function_name)."""
    if not fqn or "." not in fqn:
        return (fqn or "", "")
    parts = fqn.rsplit(".", 1)
    return parts[0], parts[1]




def _expand_package_fqns(package_fqns: List[str]) -> List[str]:
    """패키지 FQN 목록을 깊이순 모든 prefix 포함하도록 확장."""
    seen = set()
    out: List[str] = []
    for fqn in package_fqns:
        if not fqn or not fqn.strip():
            continue
        fqn = fqn.strip()
        parts = fqn.split(".")
        for i in range(1, len(parts) + 1):
            p = ".".join(parts[:i])
            if p not in seen:
                seen.add(p)
                out.append(p)
    return sorted(out, key=lambda x: (x.count("."), x))


def _refer_to_relation_type(target_type: str) -> str:
    """target_type에 따른 관계명: SqlStatement=CALLS_SQL, ConfigSection=USES_CONFIG, 그 외=REFER_TO."""
    t = (target_type or "").strip()
    if t == "SqlStatement":
        return "CALLS_SQL"
    if t == "ConfigSection":
        return "USES_CONFIG"
    return "REFER_TO"


def _target_fqn_from_module_index(module_index: Dict[str, Any], target_name: str) -> Optional[str]:
    """module_index에서 짧은 이름(target_name)으로 fqn 찾기. 없으면 None."""
    if not target_name or not isinstance(module_index, dict):
        return None
    target = (target_name or "").strip()
    if not target:
        return None
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        name = info.get("name") or _extract_short_name(fqn)
        if name and (name == target or (fqn and fqn == target)):
            return fqn
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Node MERGE — 개별 노드 생성
# ═══════════════════════════════════════════════════════════════════════════

# --- 2-1. 코드 엔티티 노드 (FUNCTION, MODULE, PACKAGE, VARIABLE) ---

def build_function_queries(function_facts_list: List[FunctionFacts], node_kinds: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Method 노드 MERGE. 속성: name(메서드명), module_name(소속 모듈 짧은이름), module_key(소속 모듈 fqn) 등.

    Args:
        node_kinds: function_id → 원래 노드타입 (FUNCTION, METHOD 등) 매핑. kind 속성에 저장.
    """
    queries = []
    for m in function_facts_list:
        module_fqn, function_name = _module_fqn_and_function_name(m.fqn)
        module_name_short = _extract_short_name(module_fqn)
        kind = (node_kinds or {}).get(m.function_id, "METHOD")
        # code_ref에서 file_path 추출 (예: "src/core/db_engine.c:575-620" → "src/core/db_engine.c")
        code_ref = m.code_ref or ""
        file_path = code_ref.split(":")[0] if ":" in code_ref else code_ref
        directory, file_name = _split_file_path(file_path)
        queries.append(_merge_node_query("FUNCTION", "function_id", m.function_id, {
            "signature": m.signature or "",
            "start_line": m.start_line, "end_line": m.end_line,
            "code_ref": code_ref, "module_key": m.module_key or "",
            "package_name": m.package_name or "",
            "module_name": module_name_short, "name": function_name,
            "code_text": (m.code or "")[:_CHUNK_CODE_MAX],
            "file_path": file_path, "directory": directory, "file_name": file_name,
        }, sub_label=kind))
    return queries


def build_module_queries(module_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """MODULE 노드 MERGE. 속성: fqn, name, type, package_name."""
    queries = []
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        if not fqn:
            continue
        name = info.get("name", "")
        if not name and fqn:
            name = _extract_short_name(fqn)
        kind = info.get("kind", "")
        queries.append(_merge_node_query("MODULE", "fqn", fqn, {
            "name": name, "type": kind,
            "package_name": info.get("package_name") or "",
        }, sub_label=kind))
    return queries


def build_package_queries(package_fqns: List[str]) -> List[Dict[str, Any]]:
    """PACKAGE 노드 MERGE (fqn 기준). 계층용 prefix도 포함하려면 _expand_package_fqns로 넣어줄 것."""
    queries = []
    for fqn in package_fqns:
        if not fqn:
            continue
        segment = _extract_short_name(fqn)
        queries.append(_merge_node_query("PACKAGE", "fqn", fqn, {"name": segment}))
    return queries


def build_variable_queries(variable_facts_list: List[VariableFacts], lang: str, node_kinds: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """VARIABLE/CONSTANT 노드 MERGE. ontology 기반으로 primary 라벨 결정.

    Args:
        node_kinds: variable_id → 원래 노드타입 (FIELD, DEFINE 등) 매핑. sub_label로 저장.
        lang: 언어 코드. ontology에서 primary 라벨(VARIABLE/CONSTANT) 결정에 사용.
    """
    from analyzer.strategy.framework.common.ontology import get_ontology
    onto = get_ontology()
    queries = []
    for f in variable_facts_list:
        module_name = _extract_short_name(f.module_key).split(":")[0]
        kind = (node_kinds or {}).get(f.variable_id, "FIELD")
        primary = onto.primary_label_for(kind, lang)
        queries.append(_merge_node_query(primary, "variable_id", f.variable_id, {
            "name": f.name, "module_name": module_name,
            "start_line": f.start_line, "end_line": f.end_line,
            "variable_type": f.variable_type or "", "code_ref": f.code_ref or "",
            "code_text": (f.code or "")[:_CHUNK_CODE_MAX],
        }, sub_label=kind))
    return queries


# --- 2-2. 비코드 엔티티 노드 (ConfigSection, ArtifactChunk, SqlStatement) ---

def build_config_section_queries(config_chunks: List[ConfigSectionChunk]) -> List[Dict[str, Any]]:
    """ConfigSection 노드 MERGE (config_key_path 기준). file_path·code_text 온톨로지 통일."""
    queries = []
    for c in config_chunks:
        queries.append(_merge_node_query("CONFIG_SECTION", "config_key_path", c.config_key_path, {
            "file_path": c.file_path or "", "start_line": c.start_line,
            "end_line": c.end_line, "code_text": (c.raw_snippet or "")[:_ARTIFACT_CODE_MAX],
        }))
    return queries


def build_artifact_chunk_queries(artifact_chunks: List[ArtifactChunk]) -> List[Dict[str, Any]]:
    """ArtifactChunk 노드 MERGE (chunk_id 기준). file_path·code_text 온톨로지 통일(코드 청크와 동일)."""
    queries = []
    for c in artifact_chunks:
        src = c.source_path or ""
        directory, file_name = _split_file_path(src)
        queries.append(_merge_node_query("ARTIFACT_CHUNK", "chunk_id", c.chunk_id, {
            "chunk_type": c.chunk_type or "", "hint_key": c.hint_key or "",
            "code_text": (c.text or "")[:_ARTIFACT_CODE_MAX],
            "file_path": src,
            "directory": directory,
            "file_name": file_name,
            "start_line": c.start_line, "end_line": c.end_line,
        }))
    return queries


def build_artifact_module_refer_to_queries(
    artifact_chunks: List[ArtifactChunk],
    module_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """(ARTIFACT_CHUNK)-[:REFER_TO]->(MODULE). EJB XML에서 추출한 related_module_fqns로 연결."""
    queries = []
    # module_index에서 short name → fqn 매핑 구축
    name_to_fqn: Dict[str, str] = {}
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        name = info.get("name") or _extract_short_name(fqn)
        if fqn:
            name_to_fqn[fqn] = fqn  # full FQN match
            if name:
                name_to_fqn.setdefault(name, fqn)  # short name fallback

    seen: set = set()
    for c in artifact_chunks:
        if not c.related_module_fqns:
            continue
        for ref_fqn in c.related_module_fqns:
            # FQN 직접 매칭 또는 short name 매칭
            target_fqn = name_to_fqn.get(ref_fqn)
            if not target_fqn:
                short = _extract_short_name(ref_fqn)
                target_fqn = name_to_fqn.get(short)
            if not target_fqn:
                continue
            key = (c.chunk_id, target_fqn)
            if key in seen:
                continue
            seen.add(key)
            q = (
                "MATCH (a:ARTIFACT_CHUNK {chunk_id: $chunk_id})\n"
                "MATCH (c:MODULE {fqn: $module_fqn})\n"
                "MERGE (a)-[r:REFER_TO]->(c)\n"
                "SET r.updated_at = timestamp()\n"
                "RETURN r"
            )
            queries.append({"query": q, "params": {"chunk_id": c.chunk_id, "module_fqn": target_fqn}})
    return queries


def build_sql_statement_queries(sql_chunks: List[SqlStatementChunk]) -> List[Dict[str, Any]]:
    """SqlStatement 노드 MERGE (sql_key 기준). sql_text·code_text(온톨로지 통일)·file_path 포함."""
    queries = []
    for s in sql_chunks:
        sql_text = (s.sql_text or "")[:_SQL_TEXT_MAX]
        queries.append(_merge_node_query("SQL_STATEMENT", "sql_key", s.sql_key, {
            "sql_type": s.sql_type, "start_line": s.start_line, "end_line": s.end_line,
            "file_path": s.file_path or "", "sql_text": sql_text, "code_text": sql_text,
        }))
    return queries


# --- 2-3. DB 노드 (Table, Column) — 복합 키/특수 로직으로 커스텀 쿼리 ---

def build_table_queries_from_reads_writes(
    reads_writes: List[ReadsWritesEdge],
    table_descriptions: Optional[Dict[Tuple[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """Reads_writes에서 등장하는 테이블로 Table 노드 MERGE. DUAL 등 제외. db/schema 빈 값은 postgres/public. description은 table_descriptions에서 적용.

    주의: DBMS 전략에서 사용하는 라벨 이름은 `Table`(대소문자 포함)이므로,
    여기서도 동일한 라벨을 사용해야 아티팩트/메서드/SQL과 DB 테이블이 하나의 노드로 합쳐진다.
    """
    seen = set()
    queries = []
    desc_map = table_descriptions or {}
    for rw in reads_writes:
        key = (rw.table_schema or TABLE_SCHEMA_DEFAULT, rw.table_name)
        if key in seen:
            continue
        name_lower = (rw.table_name or "").strip().lower()
        if name_lower in _EXCLUDED_TABLE_NAMES:
            continue
        seen.add(key)
        schema = (rw.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = rw.table_name or ""
        table_id = f"{schema}.{name}" if schema else name
        # LLM이 schema 없이 반환한 경우 ("", name) 키로도 조회
        description = desc_map.get((schema, name)) or desc_map.get(("", name)) or ""
        q = (
            "MERGE (n:Table {db: $db, schema: $schema, name: $name})\n"
            "ON CREATE SET n.created_at = timestamp(), n.table_id = $table_id,\n"
            "  n.description = CASE WHEN $description IS NOT NULL AND trim(toString($description)) <> '' THEN $description ELSE null END\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.table_id = CASE WHEN $table_id IS NOT NULL AND trim(toString($table_id)) <> '' THEN $table_id ELSE n.table_id END,\n"
            "  n.schema = CASE WHEN $schema IS NOT NULL AND trim(toString($schema)) <> '' THEN $schema ELSE n.schema END,\n"
            "  n.name = CASE WHEN $name IS NOT NULL AND trim(toString($name)) <> '' THEN $name ELSE n.name END,\n"
            "  n.description = CASE WHEN $description IS NOT NULL AND trim(toString($description)) <> '' THEN $description ELSE n.description END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {"db": TABLE_DB_DEFAULT, "schema": schema, "name": name, "table_id": table_id, "description": description or None},
        })
    return queries


def build_column_queries(column_refs: List[ColumnRef]) -> List[Dict[str, Any]]:
    """Column 노드 MERGE (fqn 기준). DUAL 등 제외."""
    seen = set()
    queries = []
    for c in column_refs:
        if (c.table_name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        _schema = (c.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        fqn = _build_column_fqn(_schema, c.table_name, c.column_name)
        if fqn in seen:
            continue
        seen.add(fqn)
        q = (
            "MERGE (n:Column {fqn: $fqn})\n"
            "ON CREATE SET n.created_at = timestamp(), n.name = $name, n.dtype = $dtype,\n"
            "  n.description = $description, n.nullable = $nullable\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.name = CASE WHEN $name IS NOT NULL AND trim(toString($name)) <> '' THEN $name ELSE n.name END,\n"
            "  n.dtype = CASE WHEN $dtype IS NOT NULL THEN $dtype ELSE n.dtype END,\n"
            "  n.description = CASE WHEN $description IS NOT NULL THEN $description ELSE n.description END,\n"
            "  n.nullable = $nullable\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "fqn": fqn, "name": c.column_name,
                "dtype": getattr(c, "dtype", None) or "",
                "description": getattr(c, "description", None) or "",
                "nullable": getattr(c, "nullable", True),
            },
        })
    return queries


def build_chunk_bundle_queries(bundles: List[ChunkBundle]) -> List[Dict[str, Any]]:
    """ChunkBundle 목록 → ChunkBundleChunk 노드 + (Method)-[:HAS_CHUNK]-> 쿼리."""
    queries = []
    _chunk_q = (
        "MERGE (c:ChunkBundleChunk {chunk_id: $chunk_id})\n"
        "ON CREATE SET c.created_at = timestamp()\n"
        "SET c.chunk_type = $chunk_type, c.parent_function_id = $parent_function_id,\n"
        "c.file_path = $file_path, c.start_line = $start_line, c.end_line = $end_line,\n"
        "c.code_text = $code_text, c.updated_at = timestamp()\n"
        "WITH c\n"
        "MERGE (m:FUNCTION {function_id: $parent_function_id})\n"
        "MERGE (m)-[:HAS_CHUNK]->(c)\n"
        "RETURN c"
    )
    _var_q = (
        "MERGE (c:ChunkBundleChunk {chunk_id: $chunk_id})\n"
        "ON CREATE SET c.created_at = timestamp()\n"
        "SET c.chunk_type = $chunk_type, c.parent_function_id = $parent_function_id,\n"
        "c.file_path = $file_path, c.start_line = $start_line, c.end_line = $end_line,\n"
        "c.code_text = $code_text, c.var_name = $var_name, c.var_type = $var_type, c.updated_at = timestamp()\n"
        "With c\n"
        "MERGE (m:FUNCTION {function_id: $parent_function_id})\n"
        "MERGE (m)-[:HAS_CHUNK]->(c)\n"
        "RETURN c"
    )
    for bundle in bundles:
        fp = bundle.file_meta.file_path or ""
        for m in bundle.methods:
            if not m.function_id:
                continue
            queries.append({"query": _chunk_q, "params": {
                "chunk_id": m.function_id, "chunk_type": "function_code",
                "parent_function_id": m.function_id, "file_path": fp,
                "start_line": m.start_line, "end_line": m.end_line,
                "code_text": (m.function_code or "")[:_CHUNK_CODE_MAX],
            }})
        for c in bundle.function_calls:
            if not c.call_id:
                continue
            queries.append({"query": _chunk_q, "params": {
                "chunk_id": c.call_id, "chunk_type": "function_call",
                "parent_function_id": c.parent_function_id, "file_path": fp,
                "start_line": c.start_line, "end_line": c.end_line,
                "code_text": (c.call_code or "")[:_CHUNK_CODE_MAX],
            }})
        for v in bundle.local_vars:
            cid = f"var:{v.parent_function_id}:{v.start_line}:{v.end_line}:{v.var_name}"
            queries.append({"query": _var_q, "params": {
                "chunk_id": cid, "chunk_type": "variable",
                "parent_function_id": v.parent_function_id, "file_path": fp,
                "start_line": v.start_line, "end_line": v.end_line,
                "code_text": (v.initializer_code or "")[:_CHUNK_CODE_MAX],
                "var_name": v.var_name or "", "var_type": v.var_type or "",
            }})
    return queries


# ═══════════════════════════════════════════════════════════════════════════
# 3. Relationship MERGE — 구조·FK·참조 관계 생성
# ═══════════════════════════════════════════════════════════════════════════

# --- 3-1. 패키지·모듈 구조 관계 (BELONGS_TO_PACKAGE, HAS_FUNCTION, HAS_VARIABLE) ---

def build_package_hierarchy_queries(package_fqns: List[str]) -> List[Dict[str, Any]]:
    """(자식 PACKAGE)-[:BELONGS_TO_PACKAGE]->(부모 PACKAGE) 관계."""
    expanded = _expand_package_fqns(package_fqns)
    queries = []
    for fqn in expanded:
        if "." not in fqn:
            continue
        parent = fqn.rsplit(".", 1)[0]
        queries.append(_merge_rel_query("PACKAGE", "fqn", fqn, "PACKAGE", "fqn", parent, "BELONGS_TO_PACKAGE"))
    return queries


def build_belongs_to_package_queries(module_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """(MODULE)-[:BELONGS_TO_PACKAGE]->(PACKAGE) 관계."""
    queries = []
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        pkg = info.get("package_name")
        if not fqn or not pkg:
            continue
        queries.append(_merge_rel_query("MODULE", "fqn", fqn, "PACKAGE", "fqn", pkg, "BELONGS_TO_PACKAGE"))
    return queries


def build_root_package_queries(
    package_fqns: List[str],
    module_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """root PACKAGE 노드 생성 + 최상위 패키지/고아 MODULE 연결.

    - root PACKAGE 노드 (fqn="root", name="root") 생성
    - 부모가 없는 최상위 패키지 → BELONGS_TO_PACKAGE → root
    - package_name이 없는 MODULE → BELONGS_TO_PACKAGE → root
    """
    queries: List[Dict[str, Any]] = []
    # 1. root PACKAGE 노드 생성
    queries.append(_merge_node_query("PACKAGE", "fqn", "root", {"name": "root"}))

    # 2. 최상위 패키지 (fqn에 .이 없는) → root 연결
    expanded = set(_expand_package_fqns(package_fqns))
    for fqn in expanded:
        if fqn and "." not in fqn:
            queries.append(_merge_rel_query(
                "PACKAGE", "fqn", fqn, "PACKAGE", "fqn", "root", "BELONGS_TO_PACKAGE",
            ))

    # 3. package_name이 없는 고아 MODULE → root 연결
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        pkg = info.get("package_name")
        if fqn and not pkg:
            queries.append(_merge_rel_query(
                "MODULE", "fqn", fqn, "PACKAGE", "fqn", "root", "BELONGS_TO_PACKAGE",
            ))

    return queries


def build_has_function_queries(
    function_facts_list: List[FunctionFacts],
    module_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """(MODULE)-[:HAS_FUNCTION]->(FUNCTION) 또는 (PACKAGE)-[:BELONGS_TO_PACKAGE]->(FUNCTION) 관계."""
    queries = []
    for m in function_facts_list:
        module_fqn = m.module_key if m.module_key else None
        if module_fqn:
            # 컨테이너 있음: MODULE → FUNCTION
            queries.append(_merge_rel_query("MODULE", "fqn", module_fqn, "FUNCTION", "function_id", m.function_id, "HAS_FUNCTION"))
        elif m.package_name:
            # 컨테이너 없음 (C FUNCTION 등): PACKAGE → FUNCTION
            queries.append(_merge_rel_query("PACKAGE", "fqn", m.package_name, "FUNCTION", "function_id", m.function_id, "BELONGS_TO_PACKAGE"))
    return queries


def build_has_variable_queries(
    variable_facts_list: List[VariableFacts],
    module_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """(MODULE)-[:HAS_VARIABLE]->(VARIABLE) 관계."""
    queries = []
    for f in variable_facts_list:
        module_fqn = f.module_key
        if not module_fqn:
            continue
        queries.append(_merge_rel_query("MODULE", "fqn", module_fqn, "VARIABLE", "variable_id", f.variable_id, "HAS_VARIABLE"))
    return queries


# --- 3-2. FK 관계 (FK_TO_COLUMN, FK_TO_TABLE) ---

def build_fk_to_column_queries(fk_edges: List[FkEdge]) -> List[Dict[str, Any]]:
    """(COLUMN)-[:FK_TO_COLUMN]->(COLUMN) 관계. DUAL 등 제외. 대소문자 무시 매칭."""
    if not fk_edges:
        return []
    queries = []
    seen: set = set()
    for e in fk_edges:
        if (e.from_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES or (e.to_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        from_col = e.from_column or ""
        to_col = e.to_column or ""
        if not from_col or not to_col:
            continue
        dedup_key = (e.from_table.lower(), from_col.lower(), e.to_table.lower(), to_col.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        from_fqn = _build_column_fqn(e.from_schema or "", e.from_table or "", from_col)
        to_fqn = _build_column_fqn(e.to_schema or "", e.to_table or "", to_col)
        if not from_fqn or not to_fqn:
            continue
        # toLower 매칭: Column.fqn 대소문자 불일치 대응
        q = (
            "MATCH (c1:Column) WHERE toLower(c1.fqn) = toLower($from_fqn)\n"
            "MATCH (c2:Column) WHERE toLower(c2.fqn) = toLower($to_fqn)\n"
            "MERGE (c1)-[r:FK_TO_COLUMN]->(c2)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({"query": q, "params": {"from_fqn": from_fqn, "to_fqn": to_fqn}})
    return queries


# --- 3-3. DB 구조 관계 (HAS_COLUMN) — 복합 키 커스텀 쿼리 ---

def build_has_column_queries(column_refs: List[ColumnRef]) -> List[Dict[str, Any]]:
    """(Table)-[:HAS_COLUMN]->(Column) 관계. DUAL 등 제외. db/schema 빈 값은 postgres/public."""
    seen = set()
    queries = []
    for c in column_refs:
        if (c.table_name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        schema = (c.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = c.table_name or ""
        fqn = _build_column_fqn(schema, name, c.column_name)
        key = (schema, name, fqn)
        if key in seen:
            continue
        seen.add(key)
        q = (
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            "MATCH (col:Column {fqn: $fqn})\n"
            "MERGE (t)-[r:HAS_COLUMN]->(col)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({"query": q, "params": {"db": TABLE_DB_DEFAULT, "schema": schema, "name": name, "fqn": fqn}})
    return queries


def build_fk_to_table_queries(fk_edges: List[FkEdge]) -> List[Dict[str, Any]]:
    """(TABLE)-[:FK_TO_TABLE]->(TABLE) 관계를 테이블쌍당 1개만 생성.

    schema 빈 값은 TABLE_SCHEMA_DEFAULT(public).
    관계 속성: source(최초 생성 시만 기록), sourceColumn, targetColumn.
    """
    if not fk_edges:
        return []
    seen: set = set()
    queries = []
    for e in fk_edges:
        from_schema = (e.from_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        from_table = e.from_table or ""
        to_schema = (e.to_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        to_table = e.to_table or ""
        if (from_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES or (to_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        # 동일 테이블쌍 중복 방지 (대소문자 무시)
        key = (from_schema.lower(), from_table.lower(), to_schema.lower(), to_table.lower())
        if key in seen:
            continue
        seen.add(key)
        # source: 최초 생성 시에만 기록 (이미 값이 있으면 유지)
        # sourceColumn, targetColumn: 항상 최신 값으로 갱신
        q = (
            "MATCH (t1:Table {db: $db}) WHERE toLower(t1.schema) = toLower($from_schema) AND toLower(t1.name) = toLower($from_table)\n"
            "MATCH (t2:Table {db: $db}) WHERE toLower(t2.schema) = toLower($to_schema) AND toLower(t2.name) = toLower($to_table)\n"
            "MERGE (t1)-[r:FK_TO_TABLE]->(t2)\n"
            "SET r.updated_at = timestamp(),\n"
            "    r.source = CASE WHEN r.source IS NULL THEN $source ELSE r.source END,\n"
            "    r.sourceColumn = $from_column, r.targetColumn = $to_column\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {
                "db": TABLE_DB_DEFAULT, "from_schema": from_schema, "from_table": from_table,
                "to_schema": to_schema, "to_table": to_table,
                "source": getattr(e, "source", "code"),
                "from_column": e.from_column or "", "to_column": e.to_column or "",
            },
        })
    return queries


# --- 3-4. 데이터 접근 관계 (SELECT/INSERT/UPDATE/DELETE, READS/WRITES, REFER_TO) ---

def build_reads_writes_queries(edges: List[ReadsWritesEdge]) -> List[Dict[str, Any]]:
    """(SqlStatement)-[:SELECT|INSERT|UPDATE|DELETE]->(Table) 관계. DUAL 등 제외. db/schema 빈 값은 postgres/public."""
    queries = []
    for e in edges:
        schema = (e.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = e.table_name or ""
        if (name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        rel = (e.relation or "REFER_TO").strip()
        if rel not in ("SELECT", "INSERT", "UPDATE", "DELETE", "REFER_TO"):
            rel = "REFER_TO"
        q = (
            "MATCH (s:SQL_STATEMENT {sql_key: $sql_key})\n"
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            f"MERGE (s)-[r:{rel}]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({"query": q, "params": {"sql_key": e.sql_key, "db": TABLE_DB_DEFAULT, "schema": schema, "name": name}})
    return queries


def build_function_table_from_writes_queries(edges: List[FunctionTableEdge]) -> List[Dict[str, Any]]:
    """(Function|Field)-[:READS|WRITES]->(Table) 관계. DUAL 등 제외. db/schema 빈 값은 postgres/public.

    테이블 매칭은 대소문자 무시(toLower). 기존 Table 노드가 있으면 MATCH로 찾아 연결하고,
    없으면 MERGE로 새로 생성한다 (DeepAgent 시나리오 4 대응).
    """
    queries = []
    for e in edges:
        schema = (e.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = e.table_name or ""
        if (name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        rel = e.relation or "READS"
        use_field = getattr(e, "variable_id", None)
        source_match = "MATCH (f:VARIABLE {variable_id: $variable_id})\n" if use_field else "MATCH (m:FUNCTION {function_id: $function_id})\n"
        source_node = "f" if use_field else "m"
        table_id = f"{schema}.{name}" if schema else name
        # 1) 대소문자 무시로 기존 테이블 찾기
        # 2) 없으면 전달된 이름으로 MERGE 생성
        # 3) 찾은(또는 생성된) 테이블에 관계 연결
        q = (
            source_match
            + "OPTIONAL MATCH (t_exist:Table {db: $db})\n"
            + "  WHERE toLower(t_exist.name) = toLower($name)\n"
            + "    AND toLower(t_exist.schema) = toLower($schema)\n"
            + "WITH " + source_node + ", t_exist\n"
            + "FOREACH (_ IN CASE WHEN t_exist IS NULL THEN [1] ELSE [] END |\n"
            + "  MERGE (t_new:Table {db: $db, schema: $schema, name: $name})\n"
            + "  ON CREATE SET t_new.created_at = timestamp(), t_new.table_id = $table_id\n"
            + ")\n"
            + "WITH " + source_node + "\n"
            + "MATCH (t:Table {db: $db})\n"
            + "  WHERE toLower(t.name) = toLower($name)\n"
            + "    AND toLower(t.schema) = toLower($schema)\n"
            + f"MERGE ({source_node})-[r:{rel}]->(t)\n"
            + "SET r.updated_at = timestamp()\n"
            + "RETURN r"
        )
        params: Dict[str, Any] = {"function_id": e.function_id, "db": TABLE_DB_DEFAULT, "schema": schema, "name": name, "table_id": table_id}
        if use_field:
            params["variable_id"] = e.variable_id
        queries.append({"query": q, "params": params})
    return queries


def build_table_from_entity_classes(
    entity_map: Dict[str, str],
    module_index: Dict[str, Any],
    variable_facts_list: Optional[List[VariableFacts]] = None,
    schema_default: str = "",
) -> List[Dict[str, Any]]:
    """엔티티로 확정된 클래스/구조체의 멤버변수로 Column + HAS_COLUMN 노드/관계 생성.

    entity_map: {fqn: table_name} — 테이블명은 호출부에서 결정.
    컬럼명 = 멤버변수명 그대로 사용.
    """
    column_refs: List[ColumnRef] = []

    # module_key → VariableFacts 매핑 구축
    vars_by_module: Dict[str, List[VariableFacts]] = {}
    for v in (variable_facts_list or []):
        if v.module_key:
            vars_by_module.setdefault(v.module_key, []).append(v)

    for fqn, table_name in entity_map.items():
        info = module_index.get(fqn)
        if not isinstance(info, dict):
            continue
        module_key = info.get("module_key", "")
        if not table_name:
            continue
        schema = schema_default or TABLE_SCHEMA_DEFAULT

        # 멤버변수(VariableFacts)에서 Column 정보 추출
        member_vars = vars_by_module.get(module_key, [])
        for v in member_vars:
            if not v.name:
                continue
            column_refs.append(ColumnRef(
                table_schema=schema,
                table_name=table_name,
                column_name=v.name,
                dtype=v.variable_type or "",
                description="",
                nullable=True,
            ))

    # Table + Column + HAS_COLUMN 쿼리 생성
    queries: List[Dict[str, Any]] = []
    table_names_seen: set = set()

    # Table 노드 MERGE
    for fqn, table_name in entity_map.items():
        if not table_name or table_name in table_names_seen:
            continue
        table_names_seen.add(table_name)
        schema = schema_default or TABLE_SCHEMA_DEFAULT
        table_id = f"{schema}.{table_name}" if schema else table_name
        q = (
            "MERGE (n:Table {db: $db, schema: $schema, name: $name})\n"
            "ON CREATE SET n.created_at = timestamp(), n.table_id = $table_id\n"
            "ON MATCH SET n.updated_at = timestamp()\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {"db": TABLE_DB_DEFAULT, "schema": schema, "name": table_name, "table_id": table_id},
        })

    # Column 노드 MERGE + HAS_COLUMN 관계
    queries.extend(build_column_queries(column_refs))
    queries.extend(build_has_column_queries(column_refs))

    return queries


def build_module_table_refer_to_queries(edges: List[ModuleTableEdge]) -> List[Dict[str, Any]]:
    """엔티티 (MODULE)-[:REFER_TO]->(TABLE) 출처 연결. DUAL 등 제외."""
    queries = []
    for e in edges:
        schema = (e.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = (e.table_name or "").strip()
        if (name or "").lower() in _EXCLUDED_TABLE_NAMES:
            continue
        fqn = (e.module_fqn or "").strip()
        if not fqn:
            continue
        q = (
            "MATCH (c:MODULE {fqn: $module_fqn})\n"
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            "MERGE (c)-[r:REFER_TO]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"module_fqn": fqn, "db": TABLE_DB_DEFAULT, "schema": schema, "name": name},
        })
    return queries


def build_artifact_table_refer_to_queries(edges: List[ArtifactTableEdge]) -> List[Dict[str, Any]]:
    """EJB XML (ARTIFACT_CHUNK)-[:REFER_TO]->(TABLE). entity_xml:: 전용."""
    queries = []
    for e in edges:
        schema = (e.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = (e.table_name or "").strip()
        if (name or "").lower() in _EXCLUDED_TABLE_NAMES:
            continue
        chunk_id = (e.chunk_id or "").strip()
        if not chunk_id:
            continue
        q = (
            "MATCH (a:ARTIFACT_CHUNK {chunk_id: $chunk_id})\n"
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            "MERGE (a)-[r:REFER_TO]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"chunk_id": chunk_id, "db": TABLE_DB_DEFAULT, "schema": schema, "name": name},
        })
    return queries


# --- 3-5. 범용 참조 관계 (CALLS_SQL, USES_CONFIG, REFER_TO) ---

def build_refer_to_queries(edges: List[ReferToEdge]) -> List[Dict[str, Any]]:
    """(Method|Field)-[:CALLS_SQL|USES_CONFIG|REFER_TO]->(SqlStatement|ConfigSection|ArtifactChunk|Class)."""
    queries = []
    for e in edges:
        target_type = (e.target_type or "SqlStatement").strip()
        rel_type = _refer_to_relation_type(target_type)
        if target_type == "SqlStatement":
            match_clause = "MATCH (t:SQL_STATEMENT {sql_key: $target_id})\n"
        elif target_type == "ConfigSection":
            match_clause = "MATCH (t:CONFIG_SECTION {config_key_path: $target_id})\n"
        elif target_type == "ArtifactChunk":
            match_clause = "MATCH (t:ARTIFACT_CHUNK {chunk_id: $target_id})\n"
        elif target_type in ("Module", "MODULE"):
            match_clause = "MATCH (t:MODULE {fqn: $target_id})\n"
        else:
            match_clause = "MATCH (t:SQL_STATEMENT {sql_key: $target_id})\n"
        source_node = "f" if getattr(e, "variable_id", None) else "m"
        source_match = (
            "MATCH (f:VARIABLE {variable_id: $variable_id})\n"
            if getattr(e, "variable_id", None)
            else "MATCH (m:FUNCTION {function_id: $function_id})\n"
        )
        q = (
            source_match
            + match_clause
            + f"MERGE ({source_node})-[r:{rel_type}]->(t)\n"
            "ON CREATE SET r.confidence = $confidence, r.updated_at = timestamp()\n"
            "ON MATCH SET r.updated_at = timestamp(),\n"
            "  r.confidence = CASE WHEN $confidence IS NOT NULL THEN $confidence ELSE r.confidence END\n"
            "RETURN r"
        )
        params: Dict[str, Any] = {
            "function_id": getattr(e, "function_id", None),
            "target_type": e.target_type,
            "target_id": e.target_id,
            "confidence": e.confidence,
        }
        if getattr(e, "variable_id", None):
            params["variable_id"] = e.variable_id
        queries.append({"query": q, "params": params})
    return queries


# ═══════════════════════════════════════════════════════════════════════════
# 4. Summary / Label — summary·stereotype 속성 및 라벨 업데이트
# ═══════════════════════════════════════════════════════════════════════════

def build_function_summary_queries(summary_by_function: Dict[str, Dict[str, Any]], lang: str) -> List[Dict[str, Any]]:
    """FUNCTION 노드에 summary·stereotype 속성 및 스테레오타입 라벨 추가."""
    import json
    from analyzer.strategy.framework.linking.summarizer import VALID_STEREOTYPES
    from analyzer.strategy.framework.common.ontology import get_ontology
    onto = get_ontology()
    valid_for_lang = onto.valid_stereotypes(lang)

    rows = []
    gwt_rows = []
    for function_id, data in summary_by_function.items():
        stereotype = (data.get("stereotype") or "").strip()
        if stereotype not in VALID_STEREOTYPES:
            stereotype = "BusinessLogic"
        summary = (data.get("summary") or "").strip()
        rows.append({"function_id": function_id, "stereotype": stereotype, "summary": summary})
        # GWT 시나리오 수집
        for sc in (data.get("scenarios") or []):
            if isinstance(sc, dict) and sc.get("title"):
                gwt_rows.append({
                    "function_id": function_id,
                    "title": sc.get("title", ""),
                    "given": sc.get("given", ""),
                    "when": sc.get("when", ""),
                    "then": sc.get("then", ""),
                })

    queries = []
    # summary·stereotype 속성 설정 + 기존 CQRS 라벨 정리
    set_clause = "SET n.updated_at = timestamp(), n.stereotype = row.stereotype, n.summary = row.summary"
    if rows:
        queries.append({
            "query": f"UNWIND $rows AS row\nMATCH (n:FUNCTION {{function_id: row.function_id}})\n{set_clause}\nREMOVE n:COMMAND, n:READMODEL\nRETURN n",
            "params": {"rows": rows},
        })

    # stereotype 라벨 (언어에 맞는 것만)
    st_groups: Dict[str, List[str]] = {}
    for function_id, data in summary_by_function.items():
        stereotype = (data.get("stereotype") or "").strip()
        if stereotype in VALID_STEREOTYPES and stereotype in valid_for_lang:
            st_groups.setdefault(stereotype, []).append(function_id)

    for label, function_ids in st_groups.items():
        queries.append({
            "query": f"UNWIND $ids AS mid\nMATCH (n:FUNCTION {{function_id: mid}})\nSET n:{label}\nRETURN n",
            "params": {"ids": function_ids},
        })

    # GWT 노드 생성 + FUNCTION→GWT 관계
    if gwt_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (f:FUNCTION {function_id: row.function_id})\n"
                "MERGE (g:GWT {function_id: row.function_id, title: row.title})\n"
                "SET g.given = row.given, g.when = row.when, g.then = row.then, g.updated_at = timestamp()\n"
                "MERGE (f)-[:HAS_GWT]->(g)\n"
                "RETURN f, g"
            ),
            "params": {"rows": gwt_rows},
        })

    return queries


def build_variable_summary_queries(summary_by_variable: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """VARIABLE 노드에 summary 속성 업데이트. UNWIND 배치."""
    if not summary_by_variable:
        return []
    rows = [{"variable_id": vid, "summary": data.get("summary", "")} for vid, data in summary_by_variable.items()]
    return [{
        "query": (
            "UNWIND $rows AS row\n"
            "MATCH (n:VARIABLE {variable_id: row.variable_id})\n"
            "SET n.updated_at = timestamp(),\n"
            "  n.summary = CASE WHEN row.summary IS NOT NULL AND trim(toString(row.summary)) <> '' THEN row.summary ELSE n.summary END\n"
            "RETURN n"
        ),
        "params": {"rows": rows},
    }]


def build_module_summary_queries(summary_by_module: Dict[str, Dict[str, Any]], lang: str) -> List[Dict[str, Any]]:
    """MODULE 노드에 summary·moduleStereotype 속성 및 언어별 레이블 업데이트 (fqn 기준). UNWIND 배치."""
    from analyzer.strategy.framework.linking.summarizer import VALID_MODULE_STEREOTYPES
    from analyzer.strategy.framework.common.ontology import get_ontology
    onto = get_ontology()

    if not summary_by_module:
        return []
    rows = []
    for fqn, data in summary_by_module.items():
        module_st = (data.get("moduleStereotype") or "").strip()
        if module_st not in VALID_MODULE_STEREOTYPES:
            module_st = ""
        rows.append({"fqn": fqn, "summary": data.get("summary", ""), "moduleStereotype": module_st})

    queries = [{
        "query": (
            "UNWIND $rows AS row\n"
            "MATCH (n:MODULE {fqn: row.fqn})\n"
            "SET n.updated_at = timestamp(),\n"
            "  n.summary = CASE WHEN row.summary IS NOT NULL AND trim(toString(row.summary)) <> '' THEN row.summary ELSE n.summary END,\n"
            "  n.moduleStereotype = row.moduleStereotype\n"
            "RETURN n"
        ),
        "params": {"rows": rows},
    }]

    # moduleStereotype 라벨 (OOP 언어만)
    if onto.is_oop(lang):
        module_st_groups: Dict[str, List[str]] = {}
        for fqn, data in summary_by_module.items():
            module_st = (data.get("moduleStereotype") or "").strip()
            if module_st in VALID_MODULE_STEREOTYPES:
                module_st_groups.setdefault(module_st, []).append(fqn)

        for label, fqns in module_st_groups.items():
            queries.append({
                "query": (
                    "UNWIND $fqns AS f\n"
                    f"MATCH (n:MODULE {{fqn: f}})\n"
                    f"SET n:{label}\n"
                    "RETURN n"
                ),
                "params": {"fqns": fqns},
            })

    return queries


# ═══════════════════════════════════════════════════════════════════════════
# 5. CALLS / USES — 호출·의존 관계 (UNWIND 배치)
# ═══════════════════════════════════════════════════════════════════════════

def build_calls_queries(edges: List[CallsEdge]) -> List[Dict[str, Any]]:
    """(METHOD)-[:CALLS]->(METHOD). UNWIND 배치: internal/external 2개 쿼리."""
    internal_rows = []
    external_rows = []
    for e in edges:
        scope = getattr(e, "scope", "internal")
        caller_cfqn, caller_mname = _module_fqn_and_function_name(e.caller_function_id)
        callee_cfqn, callee_mname = _module_fqn_and_function_name(e.callee_function_id)
        caller_module_name = _extract_short_name(caller_cfqn)
        callee_module_name = _extract_short_name(callee_cfqn)

        if scope == "external" or (e.callee_function_id or "").startswith("external:"):
            callee_module_name_ext = callee_module_name
            callee_function_name_ext = callee_mname
            if (e.callee_function_id or "").startswith("external:") and "#" in (e.callee_function_id or ""):
                _ext_class, _ext_method = (e.callee_function_id or "").split(":", 1)[1].split("#", 1)
                callee_module_name_ext = _ext_class.strip()
                callee_function_name_ext = _ext_method.strip()
            external_rows.append({
                "caller_module_name": caller_module_name, "caller_function_name": caller_mname,
                "callee_function_id": e.callee_function_id,
                "callee_module_name": callee_module_name_ext, "callee_function_name": callee_function_name_ext,
                "call_code": (e.call_code or "")[:200],
            })
        else:
            internal_rows.append({
                "caller_module_name": caller_module_name, "caller_function_name": caller_mname,
                "callee_module_name": callee_module_name, "callee_function_name": callee_mname,
                "call_code": (e.call_code or "")[:200],
            })

    queries = []
    if internal_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:FUNCTION)\n"
                "WHERE a.module_name = row.caller_module_name AND a.name = row.caller_function_name\n"
                "MATCH (b:FUNCTION)\n"
                "WHERE b.module_name = row.callee_module_name AND b.name = row.callee_function_name\n"
                "WITH a, b, row\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:CALLS]->(b)\n"
                "ON CREATE SET r.call_code = row.call_code, r.scope = 'internal', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = COALESCE(r.scope, 'internal')\n"
                "RETURN r"
            ),
            "params": {"rows": internal_rows},
        })
    if external_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:FUNCTION)\n"
                "WHERE a.module_name = row.caller_module_name AND a.name = row.caller_function_name\n"
                "MERGE (ec:MODULE:CLASS:EXTERNAL {name: row.callee_module_name})\n"
                "ON CREATE SET ec.created_at = timestamp()\n"
                "ON MATCH SET ec.updated_at = timestamp()\n"
                "MERGE (b:FUNCTION:METHOD:EXTERNAL {function_id: row.callee_function_id})\n"
                "ON CREATE SET b.created_at = timestamp(), b.module_name = row.callee_module_name, b.name = row.callee_function_name\n"
                "ON MATCH SET b.updated_at = timestamp(), b.module_name = row.callee_module_name, b.name = row.callee_function_name\n"
                "MERGE (ec)-[:HAS_FUNCTION]->(b)\n"
                "With a, b, row\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:CALLS]->(b)\n"
                "ON CREATE SET r.call_code = row.call_code, r.scope = 'external', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = 'external'\n"
                "RETURN r"
            ),
            "params": {"rows": external_rows},
        })
    return queries


def build_uses_queries(edges: List[UsesEdge]) -> List[Dict[str, Any]]:
    """(MODULE)-[:USES]->(MODULE). UNWIND 배치: internal/external 2개 쿼리."""
    internal_rows = []
    external_rows = []
    for e in edges:
        from_module_name = _extract_short_name(e.from_fqn)
        to_module_name = _extract_short_name(e.to_fqn)
        if not from_module_name or not to_module_name or from_module_name == to_module_name:
            continue
        scope = getattr(e, "scope", "internal")
        row = {"from_module_name": from_module_name, "to_module_name": to_module_name}
        if scope == "external":
            external_rows.append(row)
        else:
            internal_rows.append(row)

    queries = []
    if internal_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:MODULE)\n"
                "WHERE a.name = row.from_module_name\n"
                "MATCH (b:MODULE)\n"
                "WHERE b.name = row.to_module_name\n"
                "With a, b\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:USES]->(b)\n"
                "ON CREATE SET r.scope = 'internal', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = COALESCE(r.scope, 'internal')\n"
                "RETURN r"
            ),
            "params": {"rows": internal_rows},
        })
    if external_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:MODULE)\n"
                "WHERE a.name = row.from_module_name\n"
                "MERGE (b:MODULE:CLASS:EXTERNAL {name: row.to_module_name})\n"
                "ON CREATE SET b.created_at = timestamp()\n"
                "With a, b\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:USES]->(b)\n"
                "ON CREATE SET r.scope = 'external', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = 'external'\n"
                "RETURN r"
            ),
            "params": {"rows": external_rows},
        })
    return queries


# ═══════════════════════════════════════════════════════════════════════════
# 6. UML — 상속(EXTENDS/IMPLEMENTS), 연관(ASSOCIATION), 의존/합성/집합
# ═══════════════════════════════════════════════════════════════════════════

def build_uml_inheritance_queries(module_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """module_index의 extends_type, implements_types로 EXTENDS/IMPLEMENTS 쿼리 생성."""
    queries: List[Dict[str, Any]] = []
    _internal_q = (
        "MERGE (src:MODULE {fqn: $source_fqn})\n"
        "ON CREATE SET src.created_at = timestamp()\n"
        "ON MATCH SET src.updated_at = timestamp()\n"
        "WITH src\n"
        "MATCH (t:MODULE {fqn: $target_fqn})\n"
        "MERGE (src)-[:{}]->(t)\n"
        "RETURN 1"
    )
    _external_q = (
        "MERGE (src:MODULE {fqn: $source_fqn})\n"
        "ON CREATE SET src.created_at = timestamp()\n"
        "ON MATCH SET src.updated_at = timestamp()\n"
        "WITH src\n"
        "MERGE (t:MODULE:CLASS:EXTERNAL {name: $target_module_name})\n"
        "ON CREATE SET t.created_at = timestamp()\n"
        "ON MATCH SET t.updated_at = timestamp()\n"
        "MERGE (src)-[:{}]->(t)\n"
        "RETURN 1"
    )
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        source_fqn = info.get("fqn") or info.get("module_key")
        if not source_fqn:
            continue

        extends_type = info.get("extends_type")
        if extends_type and str(extends_type).strip():
            target_name = str(extends_type).strip()
            target_fqn = _target_fqn_from_module_index(module_index, target_name)
            if target_fqn:
                queries.append({"query": _internal_q.format("EXTENDS"), "params": {"source_fqn": source_fqn, "target_fqn": target_fqn}})
            else:
                queries.append({"query": _external_q.format("EXTENDS"), "params": {"source_fqn": source_fqn, "target_module_name": target_name}})

        implements_types = info.get("implements_types")
        if implements_types and str(implements_types).strip():
            for part in str(implements_types).split(","):
                target_name = part.strip()
                if not target_name:
                    continue
                target_fqn = _target_fqn_from_module_index(module_index, target_name)
                if target_fqn:
                    queries.append({"query": _internal_q.format("IMPLEMENTS"), "params": {"source_fqn": source_fqn, "target_fqn": target_fqn}})
                else:
                    queries.append({"query": _external_q.format("IMPLEMENTS"), "params": {"source_fqn": source_fqn, "target_module_name": target_name}})
    return queries


def build_uml_association_queries(
    module_index: Dict[str, Any],
    variable_relations_by_module: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """모듈별 fieldRelations(LLM association 결과)로 (MODULE)-[:ASSOCIATION]->(MODULE) 쿼리 생성."""
    queries: List[Dict[str, Any]] = []
    for module_key_or_fqn, member_relations in (variable_relations_by_module or {}).items():
        source_fqn = module_key_or_fqn
        if not source_fqn:
            continue
        for rel in member_relations or []:
            target_module = (rel.get("targetModule") or "").strip()
            if not target_module:
                continue
            multiplicity = rel.get("multiplicity", "1")
            member_name = rel.get("member", "")
            target_fqn = _target_fqn_from_module_index(module_index, target_module)
            if target_fqn:
                q = (
                    "MATCH (src:MODULE {fqn: $source_fqn})\n"
                    "MATCH (t:MODULE {fqn: $target_fqn})\n"
                    "MERGE (src)-[r:ASSOCIATION {member: $member_name, multiplicity: $multiplicity}]->(t)\n"
                    "RETURN r"
                )
                queries.append({"query": q, "params": {
                    "source_fqn": source_fqn, "target_fqn": target_fqn,
                    "member_name": member_name, "multiplicity": multiplicity,
                }})
            else:
                q = (
                    "MATCH (src:MODULE {fqn: $source_fqn})\n"
                    "MERGE (t:MODULE:CLASS:EXTERNAL {name: $target_module_name})\n"
                    "ON CREATE SET t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    "MERGE (src)-[r:ASSOCIATION {member: $member_name, multiplicity: $multiplicity}]->(t)\n"
                    "RETURN r"
                )
                queries.append({"query": q, "params": {
                    "source_fqn": source_fqn, "target_module_name": target_module,
                    "member_name": member_name, "multiplicity": multiplicity,
                }})
    return queries


def build_uml_dependency_queries(
    summary_by_function: Dict[str, Dict[str, Any]],
    function_facts_list: List[Any],
    module_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Step6 summary_by_function의 dependencies로 DEPENDENCY/COMPOSITION/AGGREGATION 쿼리 생성.

    UNWIND 배치: (rel_type, internal/external) 조합별로 그룹화하여 최소 쿼리 수로 실행.
    """
    method_id_to_module_fqn: Dict[str, str] = {}
    for m in function_facts_list or []:
        mid = getattr(m, "function_id", None)
        if not mid:
            continue
        module_fqn = getattr(m, "module_key", "")
        if module_fqn:
            method_id_to_module_fqn[mid] = module_fqn

    # (rel_type, is_internal) → rows
    groups: Dict[tuple, list] = {}
    seen: set = set()
    for function_id, data in (summary_by_function or {}).items():
        if not isinstance(data, dict):
            continue
        source_fqn = method_id_to_module_fqn.get(function_id)
        if not source_fqn:
            continue
        deps = data.get("dependencies") or []
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            target_module = (dep.get("targetModule") or "").strip()
            if not target_module:
                continue
            source_member = dep.get("sourceMember", "")
            usage = dep.get("usage", "")
            rel_type = (dep.get("relationType") or "DEPENDENCY").strip().upper()
            if rel_type not in ("DEPENDENCY", "COMPOSITION", "AGGREGATION", "ASSOCIATION"):
                rel_type = "DEPENDENCY"
            target_fqn = _target_fqn_from_module_index(module_index, target_module)
            is_internal = target_fqn is not None
            dup_key = (source_fqn, target_fqn or target_module, rel_type)
            if dup_key in seen:
                continue
            seen.add(dup_key)
            group_key = (rel_type, is_internal)
            groups.setdefault(group_key, []).append({
                "source_fqn": source_fqn,
                "target_fqn": target_fqn or "",
                "target_module_name": target_module,
                "source_member": source_member[:200],
                "usage": (usage or "")[:200],
            })

    queries: List[Dict[str, Any]] = []
    for (rel_type, is_internal), rows in groups.items():
        if is_internal:
            if rel_type == "DEPENDENCY":
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:MODULE {fqn: row.source_fqn})\n"
                    "MATCH (t:MODULE {fqn: row.target_fqn})\n"
                    "MERGE (src)-[r:DEPENDENCY {source_member: row.source_member, usage: row.usage}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
            else:
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:MODULE {fqn: row.source_fqn})\n"
                    "MATCH (t:MODULE {fqn: row.target_fqn})\n"
                    f"MERGE (src)-[r:{rel_type} {{source_member: row.source_member}}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
        else:
            if rel_type == "DEPENDENCY":
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:MODULE {fqn: row.source_fqn})\n"
                    "MERGE (t:MODULE:CLASS:EXTERNAL {name: row.target_module_name})\n"
                    "ON CREATE SET t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    "MERGE (src)-[r:DEPENDENCY {source_member: row.source_member, usage: row.usage}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
            else:
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:MODULE {fqn: row.source_fqn})\n"
                    "MERGE (t:MODULE:CLASS:EXTERNAL {name: row.target_module_name})\n"
                    "ON CREATE SET t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    f"MERGE (src)-[r:{rel_type} {{source_member: row.source_member}}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
        queries.append({"query": q, "params": {"rows": rows}})
    return queries


# ═══════════════════════════════════════════════════════════════════════════
# 7. UNWIND 배치 — 대량 노드용 벌크 MERGE
# ═══════════════════════════════════════════════════════════════════════════

build_packages_unwind = _build_unwind_query(
    "PACKAGE", "fqn", "n.name = row.segment")

build_modules_unwind = _build_unwind_query(
    "MODULE", "fqn",
    "n.module_key = row.module_key, n.name = row.name, n.package_name = row.package_name")

build_functions_unwind = _build_unwind_query(
    "FUNCTION", "function_id",
    "n.signature = row.signature, n.start_line = row.start_line, n.end_line = row.end_line, "
    "n.code_ref = row.code_ref, n.module_key = row.module_key, n.package_name = row.package_name, "
    "n.code_text = row.code_text")

build_variables_unwind = _build_unwind_query(
    "VARIABLE", "variable_id",
    "n.name = row.name, n.start_line = row.start_line, n.end_line = row.end_line, "
    "n.variable_type = row.variable_type, n.code_ref = row.code_ref, n.code_text = row.code_text")

build_sql_statements_unwind = _build_unwind_query(
    "SQL_STATEMENT", "sql_key",
    "n.sql_type = row.sql_type, n.start_line = row.start_line, n.end_line = row.end_line, n.file_path = row.file_path")

build_config_sections_unwind = _build_unwind_query(
    "CONFIG_SECTION", "config_key_path",
    "n.start_line = row.start_line, n.end_line = row.end_line, n.code_text = row.code_text, n.file_path = row.file_path")

build_artifact_chunks_unwind = _build_unwind_query(
    "ARTIFACT_CHUNK", "chunk_id",
    "n.chunk_type = row.chunk_type, n.hint_key = row.hint_key, n.code_text = row.code_text, "
    "n.file_path = row.file_path, n.directory = row.directory, n.file_name = row.file_name, "
    "n.start_line = row.start_line, n.end_line = row.end_line")


def build_chunk_bundle_chunks_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → ChunkBundleChunk 노드 + (Method)-[:HAS_CHUNK]-> 배치. code_text 최대 8000."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    for r in rows:
        if "code_text" in r and r["code_text"]:
            r["code_text"] = (r["code_text"] or "")[:_CHUNK_CODE_MAX]
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (c:ChunkBundleChunk {chunk_id: row.chunk_id})\n"
        "ON CREATE SET c.created_at = timestamp()\n"
        "SET c.chunk_type = row.chunk_type, c.parent_function_id = row.parent_function_id,\n"
        "c.file_path = row.file_path, c.start_line = row.start_line, c.end_line = row.end_line,\n"
        "c.code_text = row.code_text, c.var_name = row.var_name, c.var_type = row.var_type, c.updated_at = timestamp()\n"
        "WITH c, row\n"
        "MERGE (m:FUNCTION {function_id: row.parent_function_id})\n"
        "MERGE (m)-[:HAS_CHUNK]->(c)"
    )
    return {"query": q, "params": {"rows": rows}}


# ═══════════════════════════════════════════════════════════════════════════
# 8. Table Description 보강 — 메서드 summary 기반 테이블 설명 생성
# ═══════════════════════════════════════════════════════════════════════════

def build_table_description_enrichment_query() -> str:
    """READS/WRITES 관계로 연결된 메서드 summary + 컬럼 메타데이터를 수집하는 Neo4j 쿼리."""
    return (
        "MATCH (m:FUNCTION)-[rw:READS|WRITES]->(t:Table)\n"
        "WHERE m.summary IS NOT NULL AND m.summary <> ''\n"
        "WITH t, collect(DISTINCT m.summary) AS method_summaries\n"
        "WHERE size(method_summaries) > 0\n"
        "OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)\n"
        "WITH t, method_summaries,\n"
        "     collect(CASE WHEN c IS NOT NULL THEN {\n"
        "       name: c.name, dtype: c.dtype,\n"
        "       description: COALESCE(c.description, ''),\n"
        "       nullable: c.nullable, is_primary_key: c.is_primary_key\n"
        "     } END) AS columns\n"
        "RETURN t.db AS db, t.schema AS schema, t.name AS name,\n"
        "       t.description AS current_description,\n"
        "       method_summaries, columns"
    )


def build_table_description_update_queries(
    enrichments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """테이블 description 보강 쿼리 빌드.

    enrichments: [{db, schema, name, enriched_description}, ...]
    """
    queries = []
    for item in enrichments:
        q = (
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            "SET t.enriched_description = $enriched_description,\n"
            "    t.description = CASE\n"
            "      WHEN t.description IS NULL OR trim(toString(t.description)) = ''\n"
            "      THEN $enriched_description\n"
            "      ELSE t.description\n"
            "    END,\n"
            "    t.description_source = COALESCE(t.description_source, '') + CASE WHEN t.description_source IS NULL OR t.description_source = '' THEN 'code' ELSE '' END,\n"
            "    t.updated_at = timestamp()\n"
            "RETURN t"
        )
        queries.append({
            "query": q,
            "params": {
                "db": item.get("db", ""),
                "schema": item.get("schema", ""),
                "name": item.get("name", ""),
                "enriched_description": item.get("enriched_description", ""),
            },
        })
    return queries


