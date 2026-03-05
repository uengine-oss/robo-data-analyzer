"""exp 그래프 → Neo4j Cypher 쿼리 생성. 배치로 넘길 리스트 반환."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from exp.models import (
    ArtifactChunk,
    CallsEdge,
    ChunkBundle,
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

# Oracle 등 시스템/더미 테이블: TABLE 노드 및 reads_writes 관계 생성 제외 (실제 스키마 테이블이 아님)
_EXCLUDED_TABLE_NAMES = frozenset({"dual"})


def _normalize_stereotype(value: Any) -> str:
    """command/readmodel만 허용, 그 외는 readmodel."""
    s = (value or "").strip().lower()
    return s if s in ("command", "readmodel") else "readmodel"


def _class_fqn_and_method_name(fqn: Optional[str]) -> tuple[str, str]:
    """fqn(예: com.abc.Foo.bar) → (class_fqn, method_name)."""
    if not fqn or "." not in fqn:
        return (fqn or "", "")
    return fqn.rsplit(".", 1)[0], fqn.rsplit(".", 1)[1]


def build_method_queries(method_facts_list: List[MethodFacts]) -> List[Dict[str, Any]]:
    """Method 노드 MERGE. framework와 동일 속성: name(메서드명), class_name(클래스 짧은이름), class_fqn, type 등."""
    queries = []
    for m in method_facts_list:
        class_fqn, method_name = _class_fqn_and_method_name(m.fqn)
        # framework: METHOD에 name(메서드명), class_name(선언 클래스 짧은이름)
        class_name_short = class_fqn.rsplit(".", 1)[-1] if class_fqn else ""
        q = (
            "MERGE (n:METHOD {method_id: $method_id})\n"
            "ON CREATE SET n.created_at = timestamp(), n.fqn = $fqn, n.signature = $signature,\n"
            "  n.start_line = $start_line, n.end_line = $end_line, n.code_ref = $code_ref,\n"
            "  n.class_key = $class_key, n.package_name = $package_name,\n"
            "  n.class_fqn = $class_fqn, n.class_name = $class_name, n.name = $name\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.fqn = CASE WHEN $fqn IS NOT NULL AND trim(toString($fqn)) <> '' THEN $fqn ELSE n.fqn END,\n"
            "  n.signature = CASE WHEN $signature IS NOT NULL AND trim(toString($signature)) <> '' THEN $signature ELSE n.signature END,\n"
            "  n.start_line = coalesce($start_line, n.start_line), n.end_line = coalesce($end_line, n.end_line),\n"
            "  n.code_ref = CASE WHEN $code_ref IS NOT NULL AND trim(toString($code_ref)) <> '' THEN $code_ref ELSE n.code_ref END,\n"
            "  n.class_key = CASE WHEN $class_key IS NOT NULL AND trim(toString($class_key)) <> '' THEN $class_key ELSE n.class_key END,\n"
            "  n.package_name = CASE WHEN $package_name IS NOT NULL AND trim(toString($package_name)) <> '' THEN $package_name ELSE n.package_name END,\n"
            "  n.class_fqn = CASE WHEN $class_fqn IS NOT NULL AND trim(toString($class_fqn)) <> '' THEN $class_fqn ELSE n.class_fqn END,\n"
            "  n.class_name = CASE WHEN $class_name IS NOT NULL AND trim(toString($class_name)) <> '' THEN $class_name ELSE n.class_name END,\n"
            "  n.name = CASE WHEN $name IS NOT NULL AND trim(toString($name)) <> '' THEN $name ELSE n.name END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "method_id": m.method_id,
                "fqn": m.fqn,
                "signature": m.signature or "",
                "start_line": m.start_line,
                "end_line": m.end_line,
                "code_ref": m.code_ref or "",
                "class_key": m.class_key or "",
                "package_name": m.package_name or "",
                "class_fqn": class_fqn,
                "class_name": class_name_short,
                "name": method_name,
            },
        })
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
        # framework: class_name(= unit_name), type(= CLASS/INTERFACE/ENUM), name
        class_name = name
        kind = info.get("kind", "CLASS")
        q = (
            "MERGE (n:CLASS {fqn: $fqn})\n"
            "ON CREATE SET n.created_at = timestamp(), n.class_key = $class_key,\n"
            "  n.name = $name, n.class_name = $class_name, n.type = $type,\n"
            "  n.kind = $kind, n.package_name = $package_name\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.class_key = CASE WHEN $class_key IS NOT NULL AND trim(toString($class_key)) <> '' THEN $class_key ELSE n.class_key END,\n"
            "  n.name = CASE WHEN $name IS NOT NULL AND trim(toString($name)) <> '' THEN $name ELSE n.name END,\n"
            "  n.class_name = CASE WHEN $class_name IS NOT NULL AND trim(toString($class_name)) <> '' THEN $class_name ELSE n.class_name END,\n"
            "  n.type = CASE WHEN $type IS NOT NULL AND trim(toString($type)) <> '' THEN $type ELSE n.type END,\n"
            "  n.kind = CASE WHEN $kind IS NOT NULL AND trim(toString($kind)) <> '' THEN $kind ELSE n.kind END,\n"
            "  n.package_name = CASE WHEN $package_name IS NOT NULL AND trim(toString($package_name)) <> '' THEN $package_name ELSE n.package_name END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "fqn": fqn,
                "class_key": info.get("class_key", fqn),
                "name": name,
                "class_name": class_name,
                "type": kind,
                "kind": kind,
                "package_name": info.get("package_name") or "",
            },
        })
    return queries


def _expand_package_fqns(package_fqns: List[str]) -> List[str]:
    """패키지 FQN 목록을 깊이순 모든 prefix 포함하도록 확장. 예: com.kbstar.kee -> com, com.kbstar, com.kbstar.kee."""
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


def build_package_queries(package_fqns: List[str]) -> List[Dict[str, Any]]:
    """PACKAGE 노드 MERGE (fqn 기준). 계층용 prefix도 포함하려면 _expand_package_fqns로 넣어줄 것."""
    queries = []
    for fqn in package_fqns:
        if not fqn:
            continue
        segment = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
        q = (
            "MERGE (n:PACKAGE {fqn: $fqn})\n"
            "ON CREATE SET n.created_at = timestamp(), n.name = $segment\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.name = CASE WHEN $segment IS NOT NULL AND trim(toString($segment)) <> '' THEN $segment ELSE n.name END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {"fqn": fqn, "segment": segment},
        })
    return queries


def build_package_hierarchy_queries(package_fqns: List[str]) -> List[Dict[str, Any]]:
    """(자식 PACKAGE)-[:BELONGS_TO_PACKAGE]->(부모 PACKAGE) 관계. 깊이순 연결."""
    expanded = _expand_package_fqns(package_fqns)
    queries = []
    for fqn in expanded:
        if "." not in fqn:
            continue
        parent = fqn.rsplit(".", 1)[0]
        q = (
            "MATCH (child:PACKAGE {fqn: $child_fqn})\n"
            "MATCH (parent:PACKAGE {fqn: $parent_fqn})\n"
            "MERGE (child)-[r:BELONGS_TO_PACKAGE]->(parent)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"child_fqn": fqn, "parent_fqn": parent},
        })
    return queries


def build_field_queries(field_facts_list: List[FieldFacts]) -> List[Dict[str, Any]]:
    """FIELD 노드 MERGE. framework와 동일 속성: name, class_name, field_type 등."""
    queries = []
    for f in field_facts_list:
        # framework: FIELD에 name, class_name(선언 클래스 짧은이름)
        class_name = (f.class_key.rsplit(".", 1)[-1].split(":")[0] if f.class_key else "") or ""
        q = (
            "MERGE (n:FIELD {field_id: $field_id})\n"
            "ON CREATE SET n.created_at = timestamp(), n.name = $name, n.class_name = $class_name,\n"
            "  n.start_line = $start_line, n.end_line = $end_line, n.field_type = $field_type, n.code_ref = $code_ref\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.name = CASE WHEN $name IS NOT NULL AND trim(toString($name)) <> '' THEN $name ELSE n.name END,\n"
            "  n.class_name = CASE WHEN $class_name IS NOT NULL AND trim(toString($class_name)) <> '' THEN $class_name ELSE n.class_name END,\n"
            "  n.start_line = coalesce($start_line, n.start_line), n.end_line = coalesce($end_line, n.end_line),\n"
            "  n.field_type = CASE WHEN $field_type IS NOT NULL AND trim(toString($field_type)) <> '' THEN $field_type ELSE n.field_type END,\n"
            "  n.code_ref = CASE WHEN $code_ref IS NOT NULL AND trim(toString($code_ref)) <> '' THEN $code_ref ELSE n.code_ref END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "field_id": f.field_id,
                "name": f.name,
                "class_name": class_name,
                "start_line": f.start_line,
                "end_line": f.end_line,
                "field_type": f.field_type or "",
                "code_ref": f.code_ref or "",
            },
        })
    return queries


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
        q = (
            "MATCH (c:CLASS {fqn: $class_fqn})\n"
            "MATCH (p:PACKAGE {fqn: $package_fqn})\n"
            "MERGE (c)-[r:BELONGS_TO_PACKAGE]->(p)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"class_fqn": fqn, "package_fqn": pkg},
        })
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
        q = (
            "MATCH (c:CLASS {fqn: $class_fqn})\n"
            "MATCH (m:METHOD {method_id: $method_id})\n"
            "MERGE (c)-[r:HAS_METHOD]->(m)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"class_fqn": class_fqn, "method_id": m.method_id},
        })
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
        q = (
            "MATCH (c:CLASS {fqn: $class_fqn})\n"
            "MATCH (f:FIELD {field_id: $field_id})\n"
            "MERGE (c)-[r:HAS_FIELD]->(f)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"class_fqn": class_fqn, "field_id": f.field_id},
        })
    return queries


# 아티팩트/설정 청크 코드 저장 최대 (자바 ChunkBundle과 동일)
_ARTIFACT_CODE_MAX = 8000


def build_config_section_queries(config_chunks: List[ConfigSectionChunk]) -> List[Dict[str, Any]]:
    """ConfigSection 노드 MERGE (config_key_path 기준). file_path·code_text 온톨로지 통일."""
    queries = []
    for c in config_chunks:
        code_text = (c.raw_snippet or "")[:_ARTIFACT_CODE_MAX]
        q = (
            "MERGE (n:CONFIG_SECTION {config_key_path: $config_key_path})\n"
            "ON CREATE SET n.created_at = timestamp(), n.file_path = $file_path,\n"
            "  n.start_line = $start_line, n.end_line = $end_line, n.code_text = $code_text\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.file_path = CASE WHEN $file_path IS NOT NULL AND trim(toString($file_path)) <> '' THEN $file_path ELSE n.file_path END,\n"
            "  n.start_line = coalesce($start_line, n.start_line), n.end_line = coalesce($end_line, n.end_line),\n"
            "  n.code_text = CASE WHEN $code_text IS NOT NULL AND trim(toString($code_text)) <> '' THEN $code_text ELSE n.code_text END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "config_key_path": c.config_key_path,
                "file_path": c.file_path or "",
                "start_line": c.start_line,
                "end_line": c.end_line,
                "code_text": code_text,
            },
        })
    return queries


def build_artifact_chunk_queries(artifact_chunks: List[ArtifactChunk]) -> List[Dict[str, Any]]:
    """ArtifactChunk 노드 MERGE (chunk_id 기준). file_path·code_text 온톨로지 통일(자바 청크와 동일)."""
    queries = []
    for c in artifact_chunks:
        code_text = (c.text or "")[:_ARTIFACT_CODE_MAX]
        q = (
            "MERGE (n:ARTIFACT_CHUNK {chunk_id: $chunk_id})\n"
            "ON CREATE SET n.created_at = timestamp(), n.chunk_type = $chunk_type, n.hint_key = $hint_key,\n"
            "  n.code_text = $code_text, n.file_path = $file_path, n.start_line = $start_line, n.end_line = $end_line\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.chunk_type = CASE WHEN $chunk_type IS NOT NULL AND trim(toString($chunk_type)) <> '' THEN $chunk_type ELSE n.chunk_type END,\n"
            "  n.hint_key = CASE WHEN $hint_key IS NOT NULL AND trim(toString($hint_key)) <> '' THEN $hint_key ELSE n.hint_key END,\n"
            "  n.code_text = CASE WHEN $code_text IS NOT NULL AND trim(toString($code_text)) <> '' THEN $code_text ELSE n.code_text END,\n"
            "  n.file_path = CASE WHEN $file_path IS NOT NULL AND trim(toString($file_path)) <> '' THEN $file_path ELSE n.file_path END,\n"
            "  n.start_line = coalesce($start_line, n.start_line), n.end_line = coalesce($end_line, n.end_line)\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "chunk_id": c.chunk_id,
                "chunk_type": c.chunk_type or "",
                "hint_key": c.hint_key or "",
                "code_text": code_text,
                "file_path": c.source_path or "",
                "start_line": c.start_line,
                "end_line": c.end_line,
            },
        })
    return queries


# 최대 저장 코드 길이 (Neo4j 권장)
_CHUNK_CODE_MAX = 8000


def build_chunk_bundle_queries(bundles: List[ChunkBundle]) -> List[Dict[str, Any]]:
    """ChunkBundle 목록 → ChunkBundleChunk 노드 + (Method)-[:HAS_CHUNK]-> 쿼리. 각 청크 결과 저장 필수."""
    queries = []
    for bundle in bundles:
        fp = bundle.file_meta.file_path or ""
        for m in bundle.methods:
            if not m.method_id:
                continue
            code = (m.method_code or "")[:_CHUNK_CODE_MAX]
            q = (
                "MERGE (c:ChunkBundleChunk {chunk_id: $chunk_id})\n"
                "ON CREATE SET c.created_at = timestamp()\n"
                "SET c.chunk_type = $chunk_type, c.parent_method_id = $parent_method_id,\n"
                "c.file_path = $file_path, c.start_line = $start_line, c.end_line = $end_line,\n"
                "c.code_text = $code_text, c.updated_at = timestamp()\n"
                "WITH c\n"
                "MERGE (m:Method {method_id: $parent_method_id})\n"
                "MERGE (m)-[:HAS_CHUNK]->(c)\n"
                "RETURN c"
            )
            queries.append({
                "query": q,
                "params": {
                    "chunk_id": m.method_id,
                    "chunk_type": "method_code",
                    "parent_method_id": m.method_id,
                    "file_path": fp,
                    "start_line": m.start_line,
                    "end_line": m.end_line,
                    "code_text": code,
                },
            })
        for c in bundle.method_calls:
            if not c.call_id:
                continue
            code = (c.call_code or "")[:_CHUNK_CODE_MAX]
            q = (
                "MERGE (c:ChunkBundleChunk {chunk_id: $chunk_id})\n"
                "ON CREATE SET c.created_at = timestamp()\n"
                "SET c.chunk_type = $chunk_type, c.parent_method_id = $parent_method_id,\n"
                "c.file_path = $file_path, c.start_line = $start_line, c.end_line = $end_line,\n"
                "c.code_text = $code_text, c.updated_at = timestamp()\n"
                "WITH c\n"
                "MERGE (m:Method {method_id: $parent_method_id})\n"
                "MERGE (m)-[:HAS_CHUNK]->(c)\n"
                "RETURN c"
            )
            queries.append({
                "query": q,
                "params": {
                    "chunk_id": c.call_id,
                    "chunk_type": "method_call",
                    "parent_method_id": c.parent_method_id,
                    "file_path": fp,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "code_text": code,
                },
            })
        for v in bundle.variables:
            cid = f"var:{v.parent_method_id}:{v.start_line}:{v.end_line}:{v.var_name}"
            code = (v.initializer_code or "")[:_CHUNK_CODE_MAX]
            q = (
                "MERGE (c:ChunkBundleChunk {chunk_id: $chunk_id})\n"
                "ON CREATE SET c.created_at = timestamp()\n"
                "SET c.chunk_type = $chunk_type, c.parent_method_id = $parent_method_id,\n"
                "c.file_path = $file_path, c.start_line = $start_line, c.end_line = $end_line,\n"
                "c.code_text = $code_text, c.var_name = $var_name, c.var_type = $var_type, c.updated_at = timestamp()\n"
                "WITH c\n"
                "MERGE (m:Method {method_id: $parent_method_id})\n"
                "MERGE (m)-[:HAS_CHUNK]->(c)\n"
                "RETURN c"
            )
            queries.append({
                "query": q,
                "params": {
                    "chunk_id": cid,
                    "chunk_type": "variable",
                    "parent_method_id": v.parent_method_id,
                    "file_path": fp,
                    "start_line": v.start_line,
                    "end_line": v.end_line,
                    "code_text": code,
                    "var_name": v.var_name or "",
                    "var_type": v.var_type or "",
                },
            })
    return queries


# SqlStatement 노드에 저장할 구문 최대 길이 (자바 code_text와 동일 8000)
_SQL_TEXT_MAX = _ARTIFACT_CODE_MAX


def build_sql_statement_queries(sql_chunks: List[SqlStatementChunk]) -> List[Dict[str, Any]]:
    """SqlStatement 노드 MERGE (sql_key 기준). sql_text·code_text(온톨로지 통일)·file_path 포함."""
    queries = []
    for s in sql_chunks:
        sql_text = (s.sql_text or "")[:_SQL_TEXT_MAX]
        q = (
            "MERGE (n:SQL_STATEMENT {sql_key: $sql_key})\n"
            "ON CREATE SET n.created_at = timestamp(), n.sql_type = $sql_type, n.start_line = $start_line,\n"
            "  n.end_line = $end_line, n.file_path = $file_path, n.sql_text = $sql_text, n.code_text = $code_text\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.sql_type = CASE WHEN $sql_type IS NOT NULL AND trim(toString($sql_type)) <> '' THEN $sql_type ELSE n.sql_type END,\n"
            "  n.start_line = coalesce($start_line, n.start_line), n.end_line = coalesce($end_line, n.end_line),\n"
            "  n.file_path = CASE WHEN $file_path IS NOT NULL AND trim(toString($file_path)) <> '' THEN $file_path ELSE n.file_path END,\n"
            "  n.sql_text = CASE WHEN $sql_text IS NOT NULL AND trim(toString($sql_text)) <> '' THEN $sql_text ELSE n.sql_text END,\n"
            "  n.code_text = CASE WHEN $code_text IS NOT NULL AND trim(toString($code_text)) <> '' THEN $code_text ELSE n.code_text END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "sql_key": s.sql_key,
                "sql_type": s.sql_type,
                "start_line": s.start_line,
                "end_line": s.end_line,
                "file_path": s.file_path or "",
                "sql_text": sql_text,
                "code_text": sql_text,
            },
        })
    return queries


def build_table_queries_from_reads_writes(reads_writes: List[ReadsWritesEdge]) -> List[Dict[str, Any]]:
    """Reads_writes에서 등장하는 테이블로 Table 노드 MERGE. DBMS와 동일 (db, schema, name) 식별·속성.
    Oracle DUAL 등 _EXCLUDED_TABLE_NAMES에 있는 이름은 TABLE 노드 생성하지 않음."""
    seen = set()
    queries = []
    db_default = ""  # exp에서 생성 시 db 없음; DBMS 처리 시 동일 노드 갱신 가능
    for rw in reads_writes:
        key = (rw.table_schema, rw.table_name)
        if key in seen:
            continue
        name_lower = (rw.table_name or "").strip().lower()
        if name_lower in _EXCLUDED_TABLE_NAMES:
            continue
        seen.add(key)
        schema = rw.table_schema or ""
        name = rw.table_name or ""
        q = (
            "MERGE (n:TABLE {db: $db, schema: $schema, name: $name})\n"
            "ON CREATE SET n.created_at = timestamp(), n.table_id = $table_id\n"
            "ON MATCH SET n.updated_at = timestamp(),\n"
            "  n.table_id = CASE WHEN $table_id IS NOT NULL AND trim(toString($table_id)) <> '' THEN $table_id ELSE n.table_id END,\n"
            "  n.schema = CASE WHEN $schema IS NOT NULL AND trim(toString($schema)) <> '' THEN $schema ELSE n.schema END,\n"
            "  n.name = CASE WHEN $name IS NOT NULL AND trim(toString($name)) <> '' THEN $name ELSE n.name END\n"
            "RETURN n"
        )
        table_id = f"{schema}.{name}" if schema else name
        queries.append({
            "query": q,
            "params": {
                "db": db_default,
                "schema": schema,
                "name": name,
                "table_id": table_id,
            },
        })
    return queries


def _refer_to_relation_type(target_type: str) -> str:
    """target_type에 따른 관계명: SqlStatement=CALLS_SQL, ConfigSection=USES_CONFIG, 그 외=REFER_TO."""
    t = (target_type or "").strip()
    if t == "SqlStatement":
        return "CALLS_SQL"
    if t == "ConfigSection":
        return "USES_CONFIG"
    return "REFER_TO"


def build_refer_to_queries(edges: List[ReferToEdge]) -> List[Dict[str, Any]]:
    """(Method|Field)-[:CALLS_SQL|USES_CONFIG|REFER_TO]->(SqlStatement|ConfigSection|ArtifactChunk|Class). target_type별 관계명·MATCH 분기."""
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
        elif target_type == "Class" or target_type == "CLASS":
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


def build_reads_writes_queries(edges: List[ReadsWritesEdge]) -> List[Dict[str, Any]]:
    """(SqlStatement)-[:SELECT|INSERT|UPDATE|DELETE]->(Table) 관계. Table은 (db, schema, name)으로 MATCH.
    DUAL 등 _EXCLUDED_TABLE_NAMES에 있는 테이블은 관계 생성하지 않음."""
    queries = []
    db_default = ""
    for e in edges:
        schema = e.table_schema or ""
        name = e.table_name or ""
        if (name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        rel = (e.relation or "REFER_TO").strip()
        if rel not in ("SELECT", "INSERT", "UPDATE", "DELETE", "REFER_TO"):
            rel = "REFER_TO"
        q = (
            "MATCH (s:SQL_STATEMENT {sql_key: $sql_key})\n"
            "MATCH (t:TABLE {db: $db, schema: $schema, name: $name})\n"
            f"MERGE (s)-[r:{rel}]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {
                "sql_key": e.sql_key,
                "db": db_default,
                "schema": schema,
                "name": name,
            },
        })
    return queries


def build_column_queries(column_refs: List[ColumnRef]) -> List[Dict[str, Any]]:
    """Column 노드 MERGE (fqn 기준). DBMS와 동일 속성: fqn, name, dtype, description, nullable. DUAL 등 제외 테이블은 스킵."""
    seen = set()
    queries = []
    for c in column_refs:
        if (c.table_name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        if c.table_schema:
            fqn = f"{c.table_schema}.{c.table_name}.{c.column_name}"
        else:
            fqn = f"{c.table_name}.{c.column_name}"
        if fqn in seen:
            continue
        seen.add(fqn)
        q = (
            "MERGE (n:COLUMN {fqn: $fqn})\n"
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
                "fqn": fqn,
                "name": c.column_name,
                "dtype": getattr(c, "dtype", None) or "",
                "description": getattr(c, "description", None) or "",
                "nullable": getattr(c, "nullable", True),
            },
        })
    return queries


def build_has_column_queries(column_refs: List[ColumnRef]) -> List[Dict[str, Any]]:
    """(Table)-[:HAS_COLUMN]->(Column) 관계. Table은 (db, schema, name)으로 MATCH. DUAL 등 제외 테이블은 스킵."""
    seen = set()
    queries = []
    db_default = ""
    for c in column_refs:
        if (c.table_name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        schema = c.table_schema or ""
        name = c.table_name or ""
        fqn = f"{schema}.{name}.{c.column_name}" if schema else f"{name}.{c.column_name}"
        key = (schema, name, fqn)
        if key in seen:
            continue
        seen.add(key)
        q = (
            "MATCH (t:TABLE {db: $db, schema: $schema, name: $name})\n"
            "MATCH (col:COLUMN {fqn: $fqn})\n"
            "MERGE (t)-[r:HAS_COLUMN]->(col)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"db": db_default, "schema": schema, "name": name, "fqn": fqn},
        })
    return queries


def _column_fqn(schema: str, table: str, column: str) -> str:
    """COLUMN 노드 fqn: schema.table.column 또는 table.column."""
    if not column:
        return ""
    if schema:
        return f"{schema}.{table}.{column}"
    return f"{table}.{column}"


def build_fk_to_table_queries(fk_edges: List[FkEdge]) -> List[Dict[str, Any]]:
    """(TABLE)-[:FK_TO_TABLE]->(TABLE) 관계를 테이블쌍당 1개만 생성. 어떤 컬럼끼리 연결인지는 FK_TO_COLUMN으로 표현."""
    if not fk_edges:
        return []
    db_default = ""
    seen: set = set()
    queries = []
    for e in fk_edges:
        from_schema = e.from_schema or ""
        from_table = e.from_table or ""
        to_schema = e.to_schema or ""
        to_table = e.to_table or ""
        if (from_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES or (to_table or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        key = (from_schema, from_table, to_schema, to_table)
        if key in seen:
            continue
        seen.add(key)
        q = (
            "MATCH (t1:TABLE {db: $db, schema: $from_schema, name: $from_table})\n"
            "MATCH (t2:TABLE {db: $db, schema: $to_schema, name: $to_table})\n"
            "MERGE (t1)-[r:FK_TO_TABLE]->(t2)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {
                "db": db_default,
                "from_schema": from_schema,
                "from_table": from_table,
                "to_schema": to_schema,
                "to_table": to_table,
            },
        })
    return queries


def build_fk_to_column_queries(fk_edges: List[FkEdge]) -> List[Dict[str, Any]]:
    """(COLUMN)-[:FK_TO_COLUMN]->(COLUMN) 관계. 어떤 컬럼끼리 FK로 연결되는지 표현. DUAL 등 제외 테이블 참조는 스킵."""
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
        q = (
            "MATCH (c1:COLUMN {fqn: $from_fqn})\n"
            "MATCH (c2:COLUMN {fqn: $to_fqn})\n"
            "MERGE (c1)-[r:FK_TO_COLUMN]->(c2)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({
            "query": q,
            "params": {"from_fqn": from_fqn, "to_fqn": to_fqn},
        })
    return queries


def build_method_table_from_writes_queries(edges: List[MethodTableEdge]) -> List[Dict[str, Any]]:
    """(Method|Field)-[:READS|WRITES]->(Table) 관계. Table은 (db, schema, name)으로 MATCH. DUAL 등 제외 테이블은 스킵."""
    queries = []
    db_default = ""
    for e in edges:
        schema = e.table_schema or ""
        name = e.table_name or ""
        if (name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        rel = e.relation or "READS"
        use_field = getattr(e, "field_id", None)
        source_match = "MATCH (f:FIELD {field_id: $field_id})\n" if use_field else "MATCH (m:METHOD {method_id: $method_id})\n"
        source_node = "f" if use_field else "m"
        q = (
            source_match
            + "MATCH (t:TABLE {db: $db, schema: $schema, name: $name})\n"
            f"MERGE ({source_node})-[r:{rel}]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        params: Dict[str, Any] = {"method_id": e.method_id, "db": db_default, "schema": schema, "name": name}
        if use_field:
            params["field_id"] = e.field_id
        queries.append({"query": q, "params": params})
    return queries


def build_method_summary_queries(summary_by_method: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """이미 있는 METHOD 노드에 summary·stereotype 속성 및 스테레오타입 레이블(COMMAND/READMODEL)만 추가. 노드 생성 안 함."""
    queries = []
    for method_id, data in summary_by_method.items():
        ster = _normalize_stereotype(data.get("stereotype"))
        summary = (data.get("summary") or "").strip()
        # 기존 노드만 MATCH 후 속성 + 스테레오타입 레이블(COMMAND | READMODEL) 갱신
        label_set = "SET n:COMMAND" if ster == "command" else "SET n:READMODEL"
        q = (
            "MATCH (n:METHOD {method_id: $method_id})\n"
            "SET n.updated_at = timestamp(), n.stereotype = $stereotype, n.summary = $summary\n"
            "REMOVE n:COMMAND, n:READMODEL\n"
            f"{label_set}\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "method_id": method_id,
                "stereotype": ster,
                "summary": summary,
            },
        })
    return queries


def build_field_summary_queries(summary_by_field: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """FIELD 노드에 summary 속성 업데이트."""
    queries = []
    for field_id, data in summary_by_field.items():
        q = (
            "MATCH (n:FIELD {field_id: $field_id})\n"
            "SET n.updated_at = timestamp(),\n"
            "  n.summary = CASE WHEN $summary IS NOT NULL AND trim(toString($summary)) <> '' THEN $summary ELSE n.summary END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "field_id": field_id,
                "summary": data.get("summary", ""),
            },
        })
    return queries


def build_class_summary_queries(summary_by_class: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """CLASS 노드에 summary 속성 업데이트 (fqn 기준)."""
    queries = []
    for class_fqn, data in summary_by_class.items():
        q = (
            "MATCH (n:CLASS {fqn: $fqn})\n"
            "SET n.updated_at = timestamp(),\n"
            "  n.summary = CASE WHEN $summary IS NOT NULL AND trim(toString($summary)) <> '' THEN $summary ELSE n.summary END\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {
                "fqn": class_fqn,
                "summary": data.get("summary", ""),
            },
        })
    return queries


def build_user_story_queries(user_stories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """USER_STORY 노드 생성, (CLASS)-[:HAS_USER_STORY]->(USER_STORY), AC 노드 및 (USER_STORY)-[:HAS_AC]->(ACCEPTANCE_CRITERIA) 생성.
    user_stories 항목은 class_fqn, id, role, goal, benefit, acceptance_criteria 포함."""
    queries = []
    for us in user_stories or []:
        if not isinstance(us, dict):
            continue
        class_fqn = (us.get("class_fqn") or "").strip()
        story_id = (us.get("id") or "").strip() or "US-0"
        user_story_id = f"{class_fqn}|{story_id}" if class_fqn else story_id
        role = (us.get("role") or "").strip()
        goal = (us.get("goal") or "").strip()
        benefit = (us.get("benefit") or "").strip()
        ac_list = us.get("acceptance_criteria")
        acceptance_criteria_json = json.dumps(ac_list, ensure_ascii=False) if ac_list is not None else "[]"
        q = (
            "MERGE (c:CLASS {fqn: $class_fqn})\n"
            "MERGE (u:USER_STORY {user_story_id: $user_story_id})\n"
            "ON CREATE SET u.created_at = timestamp(), u.class_fqn = $class_fqn, u.story_id = $story_id,\n"
            "  u.role = $role, u.goal = $goal, u.benefit = $benefit, u.acceptance_criteria = $acceptance_criteria\n"
            "ON MATCH SET u.updated_at = timestamp(), u.role = $role, u.goal = $goal, u.benefit = $benefit,\n"
            "  u.acceptance_criteria = $acceptance_criteria\n"
            "WITH c, u\n"
            "MERGE (c)-[r:HAS_USER_STORY]->(u)\n"
            "ON CREATE SET r.created_at = timestamp()\n"
            "ON MATCH SET r.updated_at = timestamp()\n"
            "RETURN u"
        )
        queries.append({
            "query": q,
            "params": {
                "class_fqn": class_fqn,
                "user_story_id": user_story_id,
                "story_id": story_id,
                "role": role[:500] if role else "",
                "goal": goal[:1000] if goal else "",
                "benefit": benefit[:500] if benefit else "",
                "acceptance_criteria": acceptance_criteria_json[:8000] if acceptance_criteria_json else "[]",
            },
        })
        # AC 노드 및 (USER_STORY)-[:HAS_AC]->(ACCEPTANCE_CRITERIA) 생성
        for ac_idx, ac in enumerate(us.get("acceptance_criteria") or [], 1):
            if not isinstance(ac, dict):
                continue
            ac_id = ac.get("id") or f"AC-{story_id}-{ac_idx}"
            ac_node_id = f"{user_story_id}|{ac_id}"
            title = (ac.get("title") or "")[:500]
            given_val = ac.get("given")
            when_val = ac.get("when")
            then_val = ac.get("then")
            given_str = json.dumps(given_val, ensure_ascii=False)[:2000] if given_val is not None else "[]"
            when_str = json.dumps(when_val, ensure_ascii=False)[:2000] if when_val is not None else "[]"
            then_str = json.dumps(then_val, ensure_ascii=False)[:2000] if then_val is not None else "[]"
            q_ac = (
                "MATCH (u:USER_STORY {user_story_id: $user_story_id})\n"
                "MERGE (ac:ACCEPTANCE_CRITERIA {ac_id: $ac_id})\n"
                "ON CREATE SET ac.user_story_id = $user_story_id, ac.created_at = timestamp()\n"
                "ON MATCH SET ac.updated_at = timestamp()\n"
                "SET ac.title = $title, ac.given = $given, ac.when_clause = $when_clause, ac.then_clause = $then_clause\n"
                "WITH u, ac\n"
                "MERGE (u)-[r:HAS_AC]->(ac)\n"
                "ON CREATE SET r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp()\n"
                "RETURN ac"
            )
            queries.append({
                "query": q_ac,
                "params": {
                    "user_story_id": user_story_id,
                    "ac_id": ac_node_id,
                    "title": title,
                    "given": given_str,
                    "when_clause": when_str,
                    "then_clause": then_str,
                },
            })
    return queries


def build_calls_queries(edges: List[CallsEdge]) -> List[Dict[str, Any]]:
    """(METHOD)-[:CALLS]->(METHOD). 클래스명(class_name) + 메서드명(name)으로만 MATCH (속성/값 일치)."""
    queries = []
    for e in edges:
        scope = getattr(e, "scope", "internal")
        # method_id에서 클래스명(짧은이름), 메서드명 추출 → 노드의 class_name, name과 매칭
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
            q = (
                "MATCH (a:METHOD)\n"
                "WHERE a.class_name = $caller_class_name AND a.name = $caller_method_name\n"
                "MERGE (ec:CLASS:EXTERNAL_CLASS {class_name: $callee_class_name})\n"
                "ON CREATE SET ec.name = $callee_class_name, ec.created_at = timestamp()\n"
                "ON MATCH SET ec.updated_at = timestamp()\n"
                "MERGE (b:METHOD:EXTERNAL_METHOD {method_id: $callee_method_id})\n"
                "ON CREATE SET b.created_at = timestamp(), b.class_name = $callee_class_name, b.name = $callee_method_name\n"
                "ON MATCH SET b.updated_at = timestamp(), b.class_name = $callee_class_name, b.name = $callee_method_name\n"
                "MERGE (ec)-[:HAS_METHOD]->(b)\n"
                "WITH a, b\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:CALLS]->(b)\n"
                "ON CREATE SET r.call_code = $call_code, r.scope = 'external', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = 'external'\n"
                "RETURN r"
            )
            params = {
                "caller_class_name": caller_class_name,
                "caller_method_name": caller_mname,
                "callee_method_id": e.callee_method_id,
                "callee_class_name": callee_class_name_ext,
                "callee_method_name": callee_method_name_ext,
                "call_code": (e.call_code or "")[:200],
            }
        else:
            q = (
                "MATCH (a:METHOD)\n"
                "WHERE a.class_name = $caller_class_name AND a.name = $caller_method_name\n"
                "MATCH (b:METHOD)\n"
                "WHERE b.class_name = $callee_class_name AND b.name = $callee_method_name\n"
                "WITH a, b\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:CALLS]->(b)\n"
                "ON CREATE SET r.call_code = $call_code, r.scope = 'internal', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = COALESCE(r.scope, 'internal')\n"
                "RETURN r"
            )
            params = {
                "caller_class_name": caller_class_name,
                "caller_method_name": caller_mname,
                "callee_class_name": callee_class_name,
                "callee_method_name": callee_mname,
                "call_code": (e.call_code or "")[:200],
            }
        queries.append({"query": q, "params": params})
    return queries


def build_uses_queries(edges: List[UsesEdge]) -> List[Dict[str, Any]]:
    """(CLASS)-[:USES]->(CLASS). external일 때 대상은 MERGE CLASS:EXTERNAL_CLASS. INTERNAL 레이블은 사용하지 않음."""
    queries = []
    for e in edges:
        from_class_name = (e.from_fqn or "").rsplit(".", 1)[-1] if e.from_fqn else ""
        to_class_name = (e.to_fqn or "").rsplit(".", 1)[-1] if e.to_fqn else ""
        if not from_class_name or not to_class_name or from_class_name == to_class_name:
            continue
        scope = getattr(e, "scope", "internal")
        if scope == "external":
            q = (
                "MATCH (a:CLASS)\n"
                "WHERE a.class_name = $from_class_name\n"
                "MERGE (b:CLASS:EXTERNAL_CLASS {class_name: $to_class_name})\n"
                "ON CREATE SET b.name = $to_class_name\n"
                "WITH a, b\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:USES]->(b)\n"
                "ON CREATE SET r.scope = 'external', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = 'external'\n"
                "RETURN r"
            )
        else:
            q = (
                "MATCH (a:CLASS)\n"
                "WHERE a.class_name = $from_class_name\n"
                "MATCH (b:CLASS)\n"
                "WHERE b.class_name = $to_class_name\n"
                "WITH a, b\n"
                "WHERE a <> b\n"
                "MERGE (a)-[r:USES]->(b)\n"
                "ON CREATE SET r.scope = 'internal', r.created_at = timestamp()\n"
                "ON MATCH SET r.updated_at = timestamp(), r.scope = COALESCE(r.scope, 'internal')\n"
                "RETURN r"
            )
        queries.append({
            "query": q,
            "params": {
                "from_class_name": from_class_name,
                "to_class_name": to_class_name,
            },
        })
    return queries


# ---------------------------------------------------------------------------
# UML: 상속(EXTENDS/IMPLEMENTS), 연관(ASSOCIATION), 의존/합성/집합(DEPENDENCY/COMPOSITION/AGGREGATION)
# ---------------------------------------------------------------------------

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


def build_uml_inheritance_queries(class_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """class_index의 extends_type, implements_types로 EXTENDS/IMPLEMENTS 쿼리 생성. AST 기반(LLM 없음)."""
    queries: List[Dict[str, Any]] = []
    seen_src = set()
    for info in class_index.values():
        if not isinstance(info, dict):
            continue
        source_fqn = info.get("fqn") or info.get("class_key")
        if not source_fqn or source_fqn in seen_src:
            continue
        seen_src.add(source_fqn)
        source_name = info.get("name") or (source_fqn.rsplit(".", 1)[-1] if source_fqn else "")

        # EXTENDS
        extends_type = info.get("extends_type")
        if extends_type and str(extends_type).strip():
            target_name = str(extends_type).strip()
            target_fqn = _target_fqn_from_class_index(class_index, target_name)
            if target_fqn:
                q = (
                    "MERGE (src:CLASS {fqn: $source_fqn})\n"
                    "ON CREATE SET src.created_at = timestamp()\n"
                    "ON MATCH SET src.updated_at = timestamp()\n"
                    "WITH src\n"
                    "MATCH (t:CLASS {fqn: $target_fqn})\n"
                    "MERGE (src)-[:EXTENDS]->(t)\n"
                    "RETURN 1"
                )
                queries.append({"query": q, "params": {"source_fqn": source_fqn, "target_fqn": target_fqn}})
            else:
                q = (
                    "MERGE (src:CLASS {fqn: $source_fqn})\n"
                    "ON CREATE SET src.created_at = timestamp()\n"
                    "ON MATCH SET src.updated_at = timestamp()\n"
                    "WITH src\n"
                    "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: $target_class_name})\n"
                    "ON CREATE SET t.name = $target_class_name, t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    "MERGE (src)-[:EXTENDS]->(t)\n"
                    "RETURN 1"
                )
                queries.append({
                    "query": q,
                    "params": {"source_fqn": source_fqn, "target_class_name": target_name},
                })

        # IMPLEMENTS
        implements_types = info.get("implements_types")
        if implements_types and str(implements_types).strip():
            for part in str(implements_types).split(","):
                target_name = part.strip()
                if not target_name:
                    continue
                target_fqn = _target_fqn_from_class_index(class_index, target_name)
                if target_fqn:
                    q = (
                        "MERGE (src:CLASS {fqn: $source_fqn})\n"
                        "ON CREATE SET src.created_at = timestamp()\n"
                        "ON MATCH SET src.updated_at = timestamp()\n"
                        "WITH src\n"
                        "MATCH (t:CLASS {fqn: $target_fqn})\n"
                        "MERGE (src)-[:IMPLEMENTS]->(t)\n"
                        "RETURN 1"
                    )
                    queries.append({"query": q, "params": {"source_fqn": source_fqn, "target_fqn": target_fqn}})
                else:
                    q = (
                        "MERGE (src:CLASS {fqn: $source_fqn})\n"
                        "ON CREATE SET src.created_at = timestamp()\n"
                        "ON MATCH SET src.updated_at = timestamp()\n"
                        "WITH src\n"
                        "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: $target_class_name})\n"
                        "ON CREATE SET t.name = $target_class_name, t.created_at = timestamp()\n"
                        "ON MATCH SET t.updated_at = timestamp()\n"
                        "MERGE (src)-[:IMPLEMENTS]->(t)\n"
                        "RETURN 1"
                    )
                    queries.append({
                        "query": q,
                        "params": {"source_fqn": source_fqn, "target_class_name": target_name},
                    })
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
                queries.append({
                    "query": q,
                    "params": {
                        "source_fqn": source_fqn,
                        "target_fqn": target_fqn,
                        "field_name": field_name,
                        "multiplicity": multiplicity,
                    },
                })
            else:
                q = (
                    "MATCH (src:CLASS {fqn: $source_fqn})\n"
                    "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: $target_class_name})\n"
                    "ON CREATE SET t.name = $target_class_name, t.created_at = timestamp()\n"
                    "ON MATCH SET t.updated_at = timestamp()\n"
                    "MERGE (src)-[r:ASSOCIATION {field: $field_name, multiplicity: $multiplicity}]->(t)\n"
                    "RETURN r"
                )
                queries.append({
                    "query": q,
                    "params": {
                        "source_fqn": source_fqn,
                        "target_class_name": target_class,
                        "field_name": field_name,
                        "multiplicity": multiplicity,
                    },
                })
    return queries


def build_uml_dependency_queries(
    summary_by_method: Dict[str, Dict[str, Any]],
    method_facts_list: List[Any],
    class_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Step6 summary_by_method의 dependencies로 DEPENDENCY/COMPOSITION/AGGREGATION 쿼리 생성."""
    queries: List[Dict[str, Any]] = []
    key_to_fqn = _class_key_to_fqn(class_index)
    method_id_to_class_fqn: Dict[str, str] = {}
    for m in method_facts_list or []:
        mid = getattr(m, "method_id", None)
        if not mid:
            continue
        class_fqn = key_to_fqn.get(getattr(m, "class_key", "") or "")
        if class_fqn:
            method_id_to_class_fqn[mid] = class_fqn
    seen: set = set()  # (source_fqn, target_fqn_or_name, rel_type) to avoid duplicates
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
            if rel_type not in ("DEPENDENCY", "COMPOSITION", "AGGREGATION"):
                rel_type = "DEPENDENCY"
            target_fqn = _target_fqn_from_class_index(class_index, target_class)
            if target_fqn:
                key = (source_fqn, target_fqn, rel_type)
                if key in seen:
                    continue
                seen.add(key)
                if rel_type in ("COMPOSITION", "AGGREGATION"):
                    q = (
                        "MATCH (src:CLASS {fqn: $source_fqn})\n"
                        "MATCH (t:CLASS {fqn: $target_fqn})\n"
                        "MERGE (src)-[r:" + rel_type + " {source_member: $source_member}]->(t)\n"
                        "ON CREATE SET r.created_at = timestamp()\n"
                        "ON MATCH SET r.updated_at = timestamp()\n"
                        "RETURN r"
                    )
                    queries.append({
                        "query": q,
                        "params": {"source_fqn": source_fqn, "target_fqn": target_fqn, "source_member": source_member[:200]},
                    })
                else:
                    q = (
                        "MATCH (src:CLASS {fqn: $source_fqn})\n"
                        "MATCH (t:CLASS {fqn: $target_fqn})\n"
                        "MERGE (src)-[r:DEPENDENCY {source_member: $source_member, usage: $usage}]->(t)\n"
                        "ON CREATE SET r.created_at = timestamp()\n"
                        "ON MATCH SET r.updated_at = timestamp()\n"
                        "RETURN r"
                    )
                    queries.append({
                        "query": q,
                        "params": {
                            "source_fqn": source_fqn,
                            "target_fqn": target_fqn,
                            "source_member": source_member[:200],
                            "usage": (usage or "")[:200],
                        },
                    })
            else:
                key = (source_fqn, target_class, rel_type)
                if key in seen:
                    continue
                seen.add(key)
                if rel_type in ("COMPOSITION", "AGGREGATION"):
                    q = (
                        "MATCH (src:CLASS {fqn: $source_fqn})\n"
                        "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: $target_class_name})\n"
                        "ON CREATE SET t.name = $target_class_name, t.created_at = timestamp()\n"
                        "ON MATCH SET t.updated_at = timestamp()\n"
                        "MERGE (src)-[r:" + rel_type + " {source_member: $source_member}]->(t)\n"
                        "ON CREATE SET r.created_at = timestamp()\n"
                        "ON MATCH SET r.updated_at = timestamp()\n"
                        "RETURN r"
                    )
                    queries.append({
                        "query": q,
                        "params": {
                            "source_fqn": source_fqn,
                            "target_class_name": target_class,
                            "source_member": source_member[:200],
                        },
                    })
                else:
                    q = (
                        "MATCH (src:CLASS {fqn: $source_fqn})\n"
                        "MERGE (t:CLASS:EXTERNAL_CLASS {class_name: $target_class_name})\n"
                        "ON CREATE SET t.name = $target_class_name, t.created_at = timestamp()\n"
                        "ON MATCH SET t.updated_at = timestamp()\n"
                        "MERGE (src)-[r:DEPENDENCY {source_member: $source_member, usage: $usage}]->(t)\n"
                        "ON CREATE SET r.created_at = timestamp()\n"
                        "ON MATCH SET r.updated_at = timestamp()\n"
                        "RETURN r"
                    )
                    queries.append({
                        "query": q,
                        "params": {
                            "source_fqn": source_fqn,
                            "target_class_name": target_class,
                            "source_member": source_member[:200],
                            "usage": (usage or "")[:200],
                        },
                    })
    return queries


# ---------------------------------------------------------------------------
# UNWIND 배치 (40개씩 그때그때 저장용)
# ---------------------------------------------------------------------------

def build_packages_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → PACKAGE 노드 배치."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (n:PACKAGE {fqn: row.fqn})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        "SET n.name = row.segment, n.updated_at = timestamp()"
    )
    return {"query": q, "params": {"rows": rows}}


def build_classes_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → CLASS 노드 배치."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (n:CLASS {fqn: row.fqn})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        "SET n.class_key = row.class_key, n.name = row.name, n.kind = row.kind,\n"
        "n.package_name = row.package_name, n.updated_at = timestamp()"
    )
    return {"query": q, "params": {"rows": rows}}


def build_methods_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → METHOD 노드 배치."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (n:METHOD {method_id: row.method_id})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        "SET n.fqn = row.fqn, n.signature = row.signature, n.start_line = row.start_line, n.end_line = row.end_line,\n"
        "n.code_ref = row.code_ref, n.class_key = row.class_key, n.package_name = row.package_name, n.updated_at = timestamp()"
    )
    return {"query": q, "params": {"rows": rows}}


def build_fields_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → FIELD 노드 배치."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (n:FIELD {field_id: row.field_id})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        "SET n.name = row.name, n.start_line = row.start_line, n.end_line = row.end_line,\n"
        "n.field_type = row.field_type, n.code_ref = row.code_ref, n.updated_at = timestamp()"
    )
    return {"query": q, "params": {"rows": rows}}


def build_chunk_bundle_chunks_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → ChunkBundleChunk 노드 + (Method)-[:HAS_CHUNK]-> 배치. code_text 최대 8000."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    _max = _CHUNK_CODE_MAX
    for r in rows:
        if "code_text" in r and r["code_text"]:
            r["code_text"] = (r["code_text"] or "")[:_max]
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


def build_sql_statements_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → SqlStatement 노드 배치."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (n:SQL_STATEMENT {sql_key: row.sql_key})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        "SET n.sql_type = row.sql_type, n.start_line = row.start_line, n.end_line = row.end_line,\n"
        "n.file_path = row.file_path, n.updated_at = timestamp()"
    )
    return {"query": q, "params": {"rows": rows}}


def build_config_sections_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → ConfigSection 노드 배치. code_text·file_path 온톨로지."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (n:CONFIG_SECTION {config_key_path: row.config_key_path})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        "SET n.start_line = row.start_line, n.end_line = row.end_line, n.code_text = row.code_text,\n"
        "n.file_path = row.file_path, n.updated_at = timestamp()"
    )
    return {"query": q, "params": {"rows": rows}}


def build_artifact_chunks_unwind(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """UNWIND $rows → ArtifactChunk 노드 배치. code_text·file_path 온톨로지."""
    if not rows:
        return {"query": "", "params": {"rows": []}}
    q = (
        "UNWIND $rows AS row\n"
        "MERGE (n:ARTIFACT_CHUNK {chunk_id: row.chunk_id})\n"
        "ON CREATE SET n.created_at = timestamp()\n"
        "SET n.chunk_type = row.chunk_type, n.hint_key = row.hint_key, n.code_text = row.code_text,\n"
        "n.file_path = row.file_path, n.start_line = row.start_line, n.end_line = row.end_line, n.updated_at = timestamp()"
    )
    return {"query": q, "params": {"rows": rows}}
