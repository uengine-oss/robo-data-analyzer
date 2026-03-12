"""Framework Neo4j Cypher 쿼리 빌더."""

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
    ClassTableEdge,
    ConfigSectionChunk,
    ReferToEdge,
    ColumnRef,
    FkEdge,
    FieldFacts,
    MethodFacts,
    MethodTableEdge,
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

# Oracle 등 시스템/더미 테이블: TABLE 노드 및 reads_writes 관계 생성 제외
_EXCLUDED_TABLE_NAMES = EXCLUDED_TABLE_NAMES
_ARTIFACT_CODE_MAX = ARTIFACT_CODE_MAX
_CHUNK_CODE_MAX = CODE_TEXT_MAX
_SQL_TEXT_MAX = SQL_TEXT_MAX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_stereotype(value: Any) -> str:
    """command/readmodel만 허용, 그 외는 readmodel."""
    s = (value or "").strip().lower()
    return s if s in ("command", "readmodel") else "readmodel"


def _class_fqn_and_method_name(fqn: Optional[str]) -> tuple[str, str]:
    """fqn(예: com.abc.Foo.bar) → (class_fqn, method_name)."""
    if not fqn or "." not in fqn:
        return (fqn or "", "")
    return fqn.rsplit(".", 1)[0], fqn.rsplit(".", 1)[1]


def _class_key_to_fqn(class_index: Dict[str, Any]) -> Dict[str, str]:
    """class_key -> fqn 매핑."""
    out = {}
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        key = info.get("class_key")
        fqn = info.get("fqn")
        if key and fqn:
            out[key] = fqn
    return out


def _column_fqn(schema: str, table: str, column: str) -> str:
    """COLUMN 노드 fqn: schema.table.column 또는 table.column."""
    if not column:
        return ""
    return f"{schema}.{table}.{column}" if schema else f"{table}.{column}"


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


def _target_fqn_from_class_index(class_index: Dict[str, Any], target_name: str) -> Optional[str]:
    """class_index에서 짧은 이름(target_name)으로 fqn 찾기. 없으면 None."""
    if not target_name or not isinstance(class_index, dict):
        return None
    target = (target_name or "").strip()
    if not target:
        return None
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("class_key")
        name = info.get("name") or (fqn.rsplit(".", 1)[-1] if fqn else "")
        if name and (name == target or (fqn and fqn == target)):
            return fqn
    return None


# ---------------------------------------------------------------------------
# Node MERGE queries (using _merge_node_query)
# ---------------------------------------------------------------------------

def build_method_queries(method_facts_list: List[MethodFacts]) -> List[Dict[str, Any]]:
    """Method 노드 MERGE. framework와 동일 속성: name(메서드명), class_name(클래스 짧은이름), class_fqn, type 등."""
    queries = []
    for m in method_facts_list:
        class_fqn, method_name = _class_fqn_and_method_name(m.fqn)
        class_name_short = class_fqn.rsplit(".", 1)[-1] if class_fqn else ""
        queries.append(_merge_node_query("METHOD", "method_id", m.method_id, {
            "signature": m.signature or "",
            "start_line": m.start_line, "end_line": m.end_line,
            "code_ref": m.code_ref or "", "class_key": m.class_key or "",
            "package_name": m.package_name or "",
            "class_name": class_name_short, "name": method_name,
            "code_text": (m.code or "")[:_CHUNK_CODE_MAX],
        }))
    return queries


def build_class_queries(class_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """CLASS 노드 MERGE. framework와 동일 속성: class_name, name, type, package_name, fqn."""
    seen = set()
    queries = []
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("class_key")
        if not fqn or fqn in seen:
            continue
        seen.add(fqn)
        name = info.get("name", "")
        if not name and fqn:
            name = fqn.rsplit(".", 1)[-1]
        kind = info.get("kind", "CLASS")
        queries.append(_merge_node_query("CLASS", "fqn", fqn, {
            "class_key": info.get("class_key", fqn), "name": name,
            "class_name": name, "type": kind, "kind": kind,
            "package_name": info.get("package_name") or "",
        }))
    return queries


def build_package_queries(package_fqns: List[str]) -> List[Dict[str, Any]]:
    """PACKAGE 노드 MERGE (fqn 기준). 계층용 prefix도 포함하려면 _expand_package_fqns로 넣어줄 것."""
    queries = []
    for fqn in package_fqns:
        if not fqn:
            continue
        segment = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
        queries.append(_merge_node_query("PACKAGE", "fqn", fqn, {"name": segment}))
    return queries


def build_field_queries(field_facts_list: List[FieldFacts]) -> List[Dict[str, Any]]:
    """FIELD 노드 MERGE. framework와 동일 속성: name, class_name, field_type 등."""
    queries = []
    for f in field_facts_list:
        class_name = (f.class_key.rsplit(".", 1)[-1].split(":")[0] if f.class_key else "") or ""
        queries.append(_merge_node_query("FIELD", "field_id", f.field_id, {
            "name": f.name, "class_name": class_name,
            "start_line": f.start_line, "end_line": f.end_line,
            "field_type": f.field_type or "", "code_ref": f.code_ref or "",
            "code_text": (f.code or "")[:_CHUNK_CODE_MAX],
        }))
    return queries


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
    """ArtifactChunk 노드 MERGE (chunk_id 기준). file_path·code_text 온톨로지 통일(자바 청크와 동일)."""
    queries = []
    for c in artifact_chunks:
        src = c.source_path or ""
        # file_path에서 directory, file_name 추출
        if "/" in src:
            directory = src.rsplit("/", 1)[0]
            file_name = src.rsplit("/", 1)[1]
        elif "\\" in src:
            directory = src.rsplit("\\", 1)[0]
            file_name = src.rsplit("\\", 1)[1]
        else:
            directory = ""
            file_name = src
        queries.append(_merge_node_query("ARTIFACT_CHUNK", "chunk_id", c.chunk_id, {
            "chunk_type": c.chunk_type or "", "hint_key": c.hint_key or "",
            "code_text": (c.text or "")[:_ARTIFACT_CODE_MAX],
            "file_path": src,
            "directory": directory,
            "file_name": file_name,
            "start_line": c.start_line, "end_line": c.end_line,
        }))
    return queries


def build_artifact_class_refer_to_queries(
    artifact_chunks: List[ArtifactChunk],
    class_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """(ARTIFACT_CHUNK)-[:REFER_TO]->(CLASS). EJB XML에서 추출한 related_class_fqns로 연결."""
    queries = []
    # class_index에서 short name → fqn 매핑 구축
    name_to_fqn: Dict[str, str] = {}
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("class_key")
        name = info.get("name") or (fqn.rsplit(".", 1)[-1] if fqn else "")
        if fqn:
            name_to_fqn[fqn] = fqn  # full FQN match
            if name:
                name_to_fqn.setdefault(name, fqn)  # short name fallback

    seen: set = set()
    for c in artifact_chunks:
        if not c.related_class_fqns:
            continue
        for ref_fqn in c.related_class_fqns:
            # FQN 직접 매칭 또는 short name 매칭
            target_fqn = name_to_fqn.get(ref_fqn)
            if not target_fqn:
                short = ref_fqn.rsplit(".", 1)[-1] if "." in ref_fqn else ref_fqn
                target_fqn = name_to_fqn.get(short)
            if not target_fqn:
                continue
            key = (c.chunk_id, target_fqn)
            if key in seen:
                continue
            seen.add(key)
            q = (
                "MATCH (a:ARTIFACT_CHUNK {chunk_id: $chunk_id})\n"
                "MATCH (c:CLASS {fqn: $class_fqn})\n"
                "MERGE (a)-[r:REFER_TO]->(c)\n"
                "SET r.updated_at = timestamp()\n"
                "RETURN r"
            )
            queries.append({"query": q, "params": {"chunk_id": c.chunk_id, "class_fqn": target_fqn}})
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


# --- Node queries with composite keys / special logic (kept custom) ---

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
        fqn = f"{_schema}.{c.table_name}.{c.column_name}" if _schema else f"{c.table_name}.{c.column_name}"
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
        "SET c.chunk_type = $chunk_type, c.parent_method_id = $parent_method_id,\n"
        "c.file_path = $file_path, c.start_line = $start_line, c.end_line = $end_line,\n"
        "c.code_text = $code_text, c.updated_at = timestamp()\n"
        "WITH c\n"
        "MERGE (m:METHOD {method_id: $parent_method_id})\n"
        "MERGE (m)-[:HAS_CHUNK]->(c)\n"
        "RETURN c"
    )
    _var_q = (
        "MERGE (c:ChunkBundleChunk {chunk_id: $chunk_id})\n"
        "ON CREATE SET c.created_at = timestamp()\n"
        "SET c.chunk_type = $chunk_type, c.parent_method_id = $parent_method_id,\n"
        "c.file_path = $file_path, c.start_line = $start_line, c.end_line = $end_line,\n"
        "c.code_text = $code_text, c.var_name = $var_name, c.var_type = $var_type, c.updated_at = timestamp()\n"
        "WITH c\n"
        "MERGE (m:METHOD {method_id: $parent_method_id})\n"
        "MERGE (m)-[:HAS_CHUNK]->(c)\n"
        "RETURN c"
    )
    for bundle in bundles:
        fp = bundle.file_meta.file_path or ""
        for m in bundle.methods:
            if not m.method_id:
                continue
            queries.append({"query": _chunk_q, "params": {
                "chunk_id": m.method_id, "chunk_type": "method_code",
                "parent_method_id": m.method_id, "file_path": fp,
                "start_line": m.start_line, "end_line": m.end_line,
                "code_text": (m.method_code or "")[:_CHUNK_CODE_MAX],
            }})
        for c in bundle.method_calls:
            if not c.call_id:
                continue
            queries.append({"query": _chunk_q, "params": {
                "chunk_id": c.call_id, "chunk_type": "method_call",
                "parent_method_id": c.parent_method_id, "file_path": fp,
                "start_line": c.start_line, "end_line": c.end_line,
                "code_text": (c.call_code or "")[:_CHUNK_CODE_MAX],
            }})
        for v in bundle.variables:
            cid = f"var:{v.parent_method_id}:{v.start_line}:{v.end_line}:{v.var_name}"
            queries.append({"query": _var_q, "params": {
                "chunk_id": cid, "chunk_type": "variable",
                "parent_method_id": v.parent_method_id, "file_path": fp,
                "start_line": v.start_line, "end_line": v.end_line,
                "code_text": (v.initializer_code or "")[:_CHUNK_CODE_MAX],
                "var_name": v.var_name or "", "var_type": v.var_type or "",
            }})
    return queries


# ---------------------------------------------------------------------------
# Relationship queries (using _merge_rel_query)
# ---------------------------------------------------------------------------

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


def build_belongs_to_package_queries(class_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """(CLASS)-[:BELONGS_TO_PACKAGE]->(PACKAGE) 관계."""
    seen = set()
    queries = []
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("class_key")
        pkg = info.get("package_name")
        if not fqn or not pkg or (fqn, pkg) in seen:
            continue
        seen.add((fqn, pkg))
        queries.append(_merge_rel_query("CLASS", "fqn", fqn, "PACKAGE", "fqn", pkg, "BELONGS_TO_PACKAGE"))
    return queries


def build_has_method_queries(
    method_facts_list: List[MethodFacts],
    class_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """(CLASS)-[:HAS_METHOD]->(METHOD) 관계."""
    key_to_fqn = _class_key_to_fqn(class_index)
    queries = []
    for m in method_facts_list:
        class_fqn = key_to_fqn.get(m.class_key) if m.class_key else None
        if not class_fqn:
            continue
        queries.append(_merge_rel_query("CLASS", "fqn", class_fqn, "METHOD", "method_id", m.method_id, "HAS_METHOD"))
    return queries


def build_has_field_queries(
    field_facts_list: List[FieldFacts],
    class_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """(CLASS)-[:HAS_FIELD]->(FIELD) 관계."""
    key_to_fqn = _class_key_to_fqn(class_index)
    queries = []
    for f in field_facts_list:
        class_fqn = key_to_fqn.get(f.class_key)
        if not class_fqn:
            continue
        queries.append(_merge_rel_query("CLASS", "fqn", class_fqn, "FIELD", "field_id", f.field_id, "HAS_FIELD"))
    return queries


def build_fk_to_column_queries(fk_edges: List[FkEdge]) -> List[Dict[str, Any]]:
    """(COLUMN)-[:FK_TO_COLUMN]->(COLUMN) 관계. DUAL 등 제외 테이블 참조는 스킵."""
    if not fk_edges:
        return []
    queries = []
    for e in fk_edges:
        if (e.from_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES or (e.to_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        from_col = e.from_column or ""
        to_col = e.to_column or ""
        if not from_col or not to_col:
            continue
        from_fqn = _column_fqn(e.from_schema or "", e.from_table or "", from_col)
        to_fqn = _column_fqn(e.to_schema or "", e.to_table or "", to_col)
        if not from_fqn or not to_fqn:
            continue
        queries.append(_merge_rel_query("COLUMN", "fqn", from_fqn, "COLUMN", "fqn", to_fqn, "FK_TO_COLUMN"))
    return queries


# --- Relationship queries with composite keys / special logic (kept custom) ---

def build_has_column_queries(column_refs: List[ColumnRef]) -> List[Dict[str, Any]]:
    """(Table)-[:HAS_COLUMN]->(Column) 관계. DUAL 등 제외. db/schema 빈 값은 postgres/public."""
    seen = set()
    queries = []
    for c in column_refs:
        if (c.table_name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        schema = (c.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = c.table_name or ""
        fqn = f"{schema}.{name}.{c.column_name}" if schema else f"{name}.{c.column_name}"
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
    """(TABLE)-[:FK_TO_TABLE]->(TABLE) 관계를 테이블쌍당 1개만 생성. schema 빈 값은 public."""
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
        key = (from_schema, from_table, to_schema, to_table)
        if key in seen:
            continue
        seen.add(key)
        q = (
            "MATCH (t1:Table {db: $db, schema: $from_schema, name: $from_table})\n"
            "MATCH (t2:Table {db: $db, schema: $to_schema, name: $to_table})\n"
            "MERGE (t1)-[r:FK_TO_TABLE]->(t2)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"db": TABLE_DB_DEFAULT, "from_schema": from_schema, "from_table": from_table, "to_schema": to_schema, "to_table": to_table},
        })
    return queries


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


def build_method_table_from_writes_queries(edges: List[MethodTableEdge]) -> List[Dict[str, Any]]:
    """(Method|Field)-[:READS|WRITES]->(Table) 관계. DUAL 등 제외. db/schema 빈 값은 postgres/public."""
    queries = []
    for e in edges:
        schema = (e.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = e.table_name or ""
        if (name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        rel = e.relation or "READS"
        use_field = getattr(e, "field_id", None)
        source_match = "MATCH (f:FIELD {field_id: $field_id})\n" if use_field else "MATCH (m:METHOD {method_id: $method_id})\n"
        source_node = "f" if use_field else "m"
        q = (
            source_match
            + "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            f"MERGE ({source_node})-[r:{rel}]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        params: Dict[str, Any] = {"method_id": e.method_id, "db": TABLE_DB_DEFAULT, "schema": schema, "name": name}
        if use_field:
            params["field_id"] = e.field_id
        queries.append({"query": q, "params": params})
    return queries


def build_class_table_refer_to_queries(edges: List[ClassTableEdge]) -> List[Dict[str, Any]]:
    """엔티티 (CLASS)-[:REFER_TO]->(TABLE) 출처 연결. DUAL 등 제외."""
    queries = []
    for e in edges:
        schema = (e.table_schema or "").strip() or TABLE_SCHEMA_DEFAULT
        name = (e.table_name or "").strip()
        if (name or "").lower() in _EXCLUDED_TABLE_NAMES:
            continue
        fqn = (e.class_fqn or "").strip()
        if not fqn:
            continue
        q = (
            "MATCH (c:CLASS {fqn: $class_fqn})\n"
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            "MERGE (c)-[r:REFER_TO]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"class_fqn": fqn, "db": TABLE_DB_DEFAULT, "schema": schema, "name": name},
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
        elif target_type in ("Class", "CLASS"):
            match_clause = "MATCH (t:CLASS {fqn: $target_id})\n"
        else:
            match_clause = "MATCH (t:SQL_STATEMENT {sql_key: $target_id})\n"
        source_node = "f" if getattr(e, "field_id", None) else "m"
        source_match = (
            "MATCH (f:FIELD {field_id: $field_id})\n"
            if getattr(e, "field_id", None)
            else "MATCH (m:METHOD {method_id: $method_id})\n"
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
            "method_id": getattr(e, "method_id", None),
            "target_type": e.target_type,
            "target_id": e.target_id,
            "confidence": e.confidence,
        }
        if getattr(e, "field_id", None):
            params["field_id"] = e.field_id
        queries.append({"query": q, "params": params})
    return queries


# ---------------------------------------------------------------------------
# Summary / label update queries
# ---------------------------------------------------------------------------

def build_method_summary_queries(summary_by_method: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """이미 있는 METHOD 노드에 summary·stereotype 속성 및 스테레오타입 레이블(COMMAND/READMODEL)만 추가.

    UNWIND 기반 배치 처리: stereotype별(COMMAND/READMODEL) 2개 쿼리로 분할.
    """
    commands = []
    readmodels = []
    for method_id, data in summary_by_method.items():
        ster = _normalize_stereotype(data.get("stereotype"))
        summary = (data.get("summary") or "").strip()
        row = {"method_id": method_id, "stereotype": ster, "summary": summary}
        if ster == "command":
            commands.append(row)
        else:
            readmodels.append(row)

    queries = []
    if commands:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (n:METHOD {method_id: row.method_id})\n"
                "SET n.updated_at = timestamp(), n.stereotype = row.stereotype, n.summary = row.summary\n"
                "REMOVE n:COMMAND, n:READMODEL\n"
                "SET n:COMMAND\n"
                "RETURN n"
            ),
            "params": {"rows": commands},
        })
    if readmodels:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (n:METHOD {method_id: row.method_id})\n"
                "SET n.updated_at = timestamp(), n.stereotype = row.stereotype, n.summary = row.summary\n"
                "REMOVE n:COMMAND, n:READMODEL\n"
                "SET n:READMODEL\n"
                "RETURN n"
            ),
            "params": {"rows": readmodels},
        })
    return queries


def build_field_summary_queries(summary_by_field: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """FIELD 노드에 summary 속성 업데이트. UNWIND 배치."""
    if not summary_by_field:
        return []
    rows = [{"field_id": fid, "summary": data.get("summary", "")} for fid, data in summary_by_field.items()]
    return [{
        "query": (
            "UNWIND $rows AS row\n"
            "MATCH (n:FIELD {field_id: row.field_id})\n"
            "SET n.updated_at = timestamp(),\n"
            "  n.summary = CASE WHEN row.summary IS NOT NULL AND trim(toString(row.summary)) <> '' THEN row.summary ELSE n.summary END\n"
            "RETURN n"
        ),
        "params": {"rows": rows},
    }]


def build_class_summary_queries(summary_by_class: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """CLASS 노드에 summary 속성 업데이트 (fqn 기준). UNWIND 배치."""
    if not summary_by_class:
        return []
    rows = [{"fqn": fqn, "summary": data.get("summary", "")} for fqn, data in summary_by_class.items()]
    return [{
        "query": (
            "UNWIND $rows AS row\n"
            "MATCH (n:CLASS {fqn: row.fqn})\n"
            "SET n.updated_at = timestamp(),\n"
            "  n.summary = CASE WHEN row.summary IS NOT NULL AND trim(toString(row.summary)) <> '' THEN row.summary ELSE n.summary END\n"
            "RETURN n"
        ),
        "params": {"rows": rows},
    }]


# ---------------------------------------------------------------------------
# CALLS / USES queries
# ---------------------------------------------------------------------------

def build_calls_queries(edges: List[CallsEdge]) -> List[Dict[str, Any]]:
    """(METHOD)-[:CALLS]->(METHOD). UNWIND 배치: internal/external 2개 쿼리."""
    internal_rows = []
    external_rows = []
    for e in edges:
        scope = getattr(e, "scope", "internal")
        caller_cfqn, caller_mname = _class_fqn_and_method_name(e.caller_method_id)
        callee_cfqn, callee_mname = _class_fqn_and_method_name(e.callee_method_id)
        caller_class_name = caller_cfqn.rsplit(".", 1)[-1] if caller_cfqn else ""
        callee_class_name = callee_cfqn.rsplit(".", 1)[-1] if callee_cfqn else ""

        if scope == "external" or (e.callee_method_id or "").startswith("external:"):
            callee_class_name_ext = callee_class_name
            callee_method_name_ext = callee_mname
            if (e.callee_method_id or "").startswith("external:") and "#" in (e.callee_method_id or ""):
                _ext_class, _ext_method = (e.callee_method_id or "").split(":", 1)[1].split("#", 1)
                callee_class_name_ext = _ext_class.strip()
                callee_method_name_ext = _ext_method.strip()
            external_rows.append({
                "caller_class_name": caller_class_name, "caller_method_name": caller_mname,
                "callee_method_id": e.callee_method_id,
                "callee_class_name": callee_class_name_ext, "callee_method_name": callee_method_name_ext,
                "call_code": (e.call_code or "")[:200],
            })
        else:
            internal_rows.append({
                "caller_class_name": caller_class_name, "caller_method_name": caller_mname,
                "callee_class_name": callee_class_name, "callee_method_name": callee_mname,
                "call_code": (e.call_code or "")[:200],
            })

    queries = []
    if internal_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:METHOD)\n"
                "WHERE a.class_name = row.caller_class_name AND a.name = row.caller_method_name\n"
                "MATCH (b:METHOD)\n"
                "WHERE b.class_name = row.callee_class_name AND b.name = row.callee_method_name\n"
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
                "MATCH (a:METHOD)\n"
                "WHERE a.class_name = row.caller_class_name AND a.name = row.caller_method_name\n"
                "MERGE (ec:CLASS:EXTERNAL_CLASS {class_name: row.callee_class_name})\n"
                "ON CREATE SET ec.name = row.callee_class_name, ec.created_at = timestamp()\n"
                "ON MATCH SET ec.updated_at = timestamp()\n"
                "MERGE (b:METHOD:EXTERNAL_METHOD {method_id: row.callee_method_id})\n"
                "ON CREATE SET b.created_at = timestamp(), b.class_name = row.callee_class_name, b.name = row.callee_method_name\n"
                "ON MATCH SET b.updated_at = timestamp(), b.class_name = row.callee_class_name, b.name = row.callee_method_name\n"
                "MERGE (ec)-[:HAS_METHOD]->(b)\n"
                "WITH a, b, row\n"
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
    """(CLASS)-[:USES]->(CLASS). UNWIND 배치: internal/external 2개 쿼리."""
    internal_rows = []
    external_rows = []
    for e in edges:
        from_class_name = (e.from_fqn or "").rsplit(".", 1)[-1] if e.from_fqn else ""
        to_class_name = (e.to_fqn or "").rsplit(".", 1)[-1] if e.to_fqn else ""
        if not from_class_name or not to_class_name or from_class_name == to_class_name:
            continue
        scope = getattr(e, "scope", "internal")
        row = {"from_class_name": from_class_name, "to_class_name": to_class_name}
        if scope == "external":
            external_rows.append(row)
        else:
            internal_rows.append(row)

    queries = []
    if internal_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:CLASS)\n"
                "WHERE a.class_name = row.from_class_name\n"
                "MATCH (b:CLASS)\n"
                "WHERE b.class_name = row.to_class_name\n"
                "WITH a, b\n"
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
                "MATCH (a:CLASS)\n"
                "WHERE a.class_name = row.from_class_name\n"
                "MERGE (b:CLASS:EXTERNAL_CLASS {class_name: row.to_class_name})\n"
                "ON CREATE SET b.name = row.to_class_name\n"
                "WITH a, b\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:USES]->(b)\n"
                "ON CREATE SET r.scope = 'external', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = 'external'\n"
                "RETURN r"
            ),
            "params": {"rows": external_rows},
        })
    return queries


# ---------------------------------------------------------------------------
# UML: 상속(EXTENDS/IMPLEMENTS), 연관(ASSOCIATION), 의존/합성/집합
# ---------------------------------------------------------------------------

def build_uml_inheritance_queries(class_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """class_index의 extends_type, implements_types로 EXTENDS/IMPLEMENTS 쿼리 생성."""
    queries: List[Dict[str, Any]] = []
    seen_src = set()
    _internal_q = (
        "MERGE (src:CLASS {fqn: $source_fqn})\n"
        "ON CREATE SET src.created_at = timestamp()\n"
        "ON MATCH SET src.updated_at = timestamp()\n"
        "WITH src\n"
        "MATCH (t:CLASS {fqn: $target_fqn})\n"
        "MERGE (src)-[:{}]->(t)\n"
        "RETURN 1"
    )
    _external_q = (
        "MERGE (src:CLASS {fqn: $source_fqn})\n"
        "ON CREATE SET src.created_at = timestamp()\n"
        "ON MATCH SET src.updated_at = timestamp()\n"
        "WITH src\n"
        "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: $target_class_name})\n"
        "ON CREATE SET t.name = $target_class_name, t.created_at = timestamp()\n"
        "ON MATCH SET t.updated_at = timestamp()\n"
        "MERGE (src)-[:{}]->(t)\n"
        "RETURN 1"
    )
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        source_fqn = info.get("fqn") or info.get("class_key")
        if not source_fqn or source_fqn in seen_src:
            continue
        seen_src.add(source_fqn)

        extends_type = info.get("extends_type")
        if extends_type and str(extends_type).strip():
            target_name = str(extends_type).strip()
            target_fqn = _target_fqn_from_class_index(class_index, target_name)
            if target_fqn:
                queries.append({"query": _internal_q.format("EXTENDS"), "params": {"source_fqn": source_fqn, "target_fqn": target_fqn}})
            else:
                queries.append({"query": _external_q.format("EXTENDS"), "params": {"source_fqn": source_fqn, "target_class_name": target_name}})

        implements_types = info.get("implements_types")
        if implements_types and str(implements_types).strip():
            for part in str(implements_types).split(","):
                target_name = part.strip()
                if not target_name:
                    continue
                target_fqn = _target_fqn_from_class_index(class_index, target_name)
                if target_fqn:
                    queries.append({"query": _internal_q.format("IMPLEMENTS"), "params": {"source_fqn": source_fqn, "target_fqn": target_fqn}})
                else:
                    queries.append({"query": _external_q.format("IMPLEMENTS"), "params": {"source_fqn": source_fqn, "target_class_name": target_name}})
    return queries


def build_uml_association_queries(
    class_index: Dict[str, Any],
    field_relations_by_class: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """클래스별 fieldRelations(LLM association 결과)로 (CLASS)-[:ASSOCIATION]->(CLASS) 쿼리 생성."""
    queries: List[Dict[str, Any]] = []
    key_to_fqn = _class_key_to_fqn(class_index)
    for class_key_or_fqn, field_relations in (field_relations_by_class or {}).items():
        source_fqn = key_to_fqn.get(class_key_or_fqn) or (
            class_index.get(class_key_or_fqn, {}).get("fqn") if isinstance(class_index.get(class_key_or_fqn), dict) else class_key_or_fqn
        )
        if not source_fqn:
            continue
        for rel in field_relations or []:
            target_class = (rel.get("targetClass") or "").strip()
            if not target_class:
                continue
            multiplicity = rel.get("multiplicity", "1")
            field_name = rel.get("field", "")
            target_fqn = _target_fqn_from_class_index(class_index, target_class)
            if target_fqn:
                q = (
                    "MATCH (src:CLASS {fqn: $source_fqn})\n"
                    "MATCH (t:CLASS {fqn: $target_fqn})\n"
                    "MERGE (src)-[r:ASSOCIATION {field: $field_name, multiplicity: $multiplicity}]->(t)\n"
                    "RETURN r"
                )
                queries.append({"query": q, "params": {
                    "source_fqn": source_fqn, "target_fqn": target_fqn,
                    "field_name": field_name, "multiplicity": multiplicity,
                }})
            else:
                q = (
                    "MATCH (src:CLASS {fqn: $source_fqn})\n"
                    "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: $target_class_name})\n"
                    "ON CREATE SET t.name = $target_class_name, t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    "MERGE (src)-[r:ASSOCIATION {field: $field_name, multiplicity: $multiplicity}]->(t)\n"
                    "RETURN r"
                )
                queries.append({"query": q, "params": {
                    "source_fqn": source_fqn, "target_class_name": target_class,
                    "field_name": field_name, "multiplicity": multiplicity,
                }})
    return queries


def build_uml_dependency_queries(
    summary_by_method: Dict[str, Dict[str, Any]],
    method_facts_list: List[Any],
    class_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Step6 summary_by_method의 dependencies로 DEPENDENCY/COMPOSITION/AGGREGATION 쿼리 생성.

    UNWIND 배치: (rel_type, internal/external) 조합별로 그룹화하여 최소 쿼리 수로 실행.
    """
    key_to_fqn = _class_key_to_fqn(class_index)
    method_id_to_class_fqn: Dict[str, str] = {}
    for m in method_facts_list or []:
        mid = getattr(m, "method_id", None)
        if not mid:
            continue
        class_fqn = key_to_fqn.get(getattr(m, "class_key", "") or "")
        if class_fqn:
            method_id_to_class_fqn[mid] = class_fqn

    # (rel_type, is_internal) → rows
    groups: Dict[tuple, list] = {}
    seen: set = set()
    for method_id, data in (summary_by_method or {}).items():
        if not isinstance(data, dict):
            continue
        source_fqn = method_id_to_class_fqn.get(method_id)
        if not source_fqn:
            continue
        deps = data.get("dependencies") or []
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            target_class = (dep.get("targetClass") or "").strip()
            if not target_class:
                continue
            source_member = dep.get("sourceMember", "")
            usage = dep.get("usage", "")
            rel_type = (dep.get("relationType") or "DEPENDENCY").strip().upper()
            if rel_type not in ("DEPENDENCY", "COMPOSITION", "AGGREGATION", "ASSOCIATION"):
                rel_type = "DEPENDENCY"
            target_fqn = _target_fqn_from_class_index(class_index, target_class)
            is_internal = target_fqn is not None
            dup_key = (source_fqn, target_fqn or target_class, rel_type)
            if dup_key in seen:
                continue
            seen.add(dup_key)
            group_key = (rel_type, is_internal)
            groups.setdefault(group_key, []).append({
                "source_fqn": source_fqn,
                "target_fqn": target_fqn or "",
                "target_class_name": target_class,
                "source_member": source_member[:200],
                "usage": (usage or "")[:200],
            })

    queries: List[Dict[str, Any]] = []
    for (rel_type, is_internal), rows in groups.items():
        if is_internal:
            if rel_type == "DEPENDENCY":
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:CLASS {fqn: row.source_fqn})\n"
                    "MATCH (t:CLASS {fqn: row.target_fqn})\n"
                    "MERGE (src)-[r:DEPENDENCY {source_member: row.source_member, usage: row.usage}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
            else:
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:CLASS {fqn: row.source_fqn})\n"
                    "MATCH (t:CLASS {fqn: row.target_fqn})\n"
                    f"MERGE (src)-[r:{rel_type} {{source_member: row.source_member}}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
        else:
            if rel_type == "DEPENDENCY":
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:CLASS {fqn: row.source_fqn})\n"
                    "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: row.target_class_name})\n"
                    "ON CREATE SET t.name = row.target_class_name, t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    "MERGE (src)-[r:DEPENDENCY {source_member: row.source_member, usage: row.usage}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
            else:
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:CLASS {fqn: row.source_fqn})\n"
                    "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: row.target_class_name})\n"
                    "ON CREATE SET t.name = row.target_class_name, t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    f"MERGE (src)-[r:{rel_type} {{source_member: row.source_member}}]->(t)\n"
                    "ON CREATE SET r.created_at = timestamp()\n"
                    "ON MATCH SET r.updated_at = timestamp()\n"
                    "RETURN r"
                )
        queries.append({"query": q, "params": {"rows": rows}})
    return queries


# ---------------------------------------------------------------------------
# UNWIND 배치 (그때그때 저장용)
# ---------------------------------------------------------------------------

build_packages_unwind = _build_unwind_query(
    "PACKAGE", "fqn", "n.name = row.segment")

build_classes_unwind = _build_unwind_query(
    "CLASS", "fqn",
    "n.class_key = row.class_key, n.name = row.name, n.kind = row.kind, n.package_name = row.package_name")

build_methods_unwind = _build_unwind_query(
    "METHOD", "method_id",
    "n.signature = row.signature, n.start_line = row.start_line, n.end_line = row.end_line, "
    "n.code_ref = row.code_ref, n.class_key = row.class_key, n.package_name = row.package_name, "
    "n.code_text = row.code_text")

build_fields_unwind = _build_unwind_query(
    "FIELD", "field_id",
    "n.name = row.name, n.start_line = row.start_line, n.end_line = row.end_line, "
    "n.field_type = row.field_type, n.code_ref = row.code_ref, n.code_text = row.code_text")

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
        "SET c.chunk_type = row.chunk_type, c.parent_method_id = row.parent_method_id,\n"
        "c.file_path = row.file_path, c.start_line = row.start_line, c.end_line = row.end_line,\n"
        "c.code_text = row.code_text, c.var_name = row.var_name, c.var_type = row.var_type, c.updated_at = timestamp()\n"
        "WITH c, row\n"
        "MERGE (m:METHOD {method_id: row.parent_method_id})\n"
        "MERGE (m)-[:HAS_CHUNK]->(c)"
    )
    return {"query": q, "params": {"rows": rows}}


# ---------------------------------------------------------------------------
# Table Description 보강 (메서드 summary 기반)
# ---------------------------------------------------------------------------

def build_table_description_enrichment_query() -> str:
    """READS/WRITES 관계로 연결된 메서드 summary를 수집하는 Neo4j 쿼리."""
    return (
        "MATCH (m:METHOD)-[rw:READS|WRITES]->(t:Table)\n"
        "WHERE m.summary IS NOT NULL AND m.summary <> ''\n"
        "WITH t, collect(DISTINCT m.summary) AS method_summaries\n"
        "WHERE size(method_summaries) > 0\n"
        "RETURN t.db AS db, t.schema AS schema, t.name AS name,\n"
        "       t.description AS current_description,\n"
        "       method_summaries"
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
