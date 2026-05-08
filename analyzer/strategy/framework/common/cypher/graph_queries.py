"""Framework Neo4j Cypher 쿼리 빌더.

Section 1: Generic query builders (MERGE/UNWIND helpers)
Section 2: 42개 specific query builder functions

Section 2 세부 구분:
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

import logging
from typing import Any, Dict, List, Optional, Tuple

from analyzer.common.pipeline.example_mapping import rule_hash
from analyzer.common.pipeline.example_mapping import collect_example_rows

logger = logging.getLogger(__name__)

from analyzer.strategy.framework.common.models.node_models import (
    ArtifactChunk,
    ArtifactTableEdge,
    CallsEdge,
    ModuleTableEdge,
    ConfigSectionChunk,
    InitByEdge,
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
from analyzer.strategy.framework.common.domain.analysis_constants import (
    EXCLUDED_TABLE_NAMES,
    ARTIFACT_CODE_MAX,
    CODE_TEXT_MAX,
    SQL_TEXT_MAX,
)
from analyzer.strategy.framework.analysis.ast_analyzer import VALID_MODULE_STEREOTYPES
from analyzer.common.cypher.schema_resolver import resolve_schema
from config.settings import settings
from analyzer.strategy.framework.common.domain.ontology import get_ontology


def _resolve_schema(schema: str) -> str:
    """Framework schema 결정 — `analyzer.common.cypher.schema_resolver.resolve_schema` 위계 적용.

    Framework 는 own_schema 개념 X (코드 안에 PROCEDURE 정의 없음).
    위계: 1) explicit → 2) "" (강제 'public' X). DDL 매칭 단계 없음.
    """
    return resolve_schema(
        explicit_schema=schema or "",
        own_schema="",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Generic query builders (MERGE / UNWIND helpers)
# ═══════════════════════════════════════════════════════════════════════════


def _label_str(primary: str, sub_label: Optional[str]) -> str:
    """primary 와 sub_label 결합 — 같으면 sub 생략 (Neo4j 동일 라벨 중복 불가)."""
    if sub_label and sub_label != primary:
        return f"{primary}:{sub_label}"
    return primary


def _external_module_label_str(lang: str) -> str:
    paradigm = get_ontology().paradigm(lang)
    if paradigm == "oop":
        return "MODULE:CLASS:EXTERNAL"
    return "MODULE:EXTERNAL"


def _external_function_label_str(lang: str) -> str:
    paradigm = get_ontology().paradigm(lang)
    if paradigm == "oop":
        return "FUNCTION:METHOD:EXTERNAL"
    return "FUNCTION:EXTERNAL"


def _parse_external_function_id(callee_function_id: str) -> Tuple[str, str]:
    if not callee_function_id or not callee_function_id.startswith("external:"):
        return ("", "")
    suffix = callee_function_id.split(":", 1)[1]
    if "#" in suffix:
        mod, fn = suffix.split("#", 1)
        return (mod.strip(), fn.strip())
    name = suffix.strip()
    return (name, name)


def _unwind_node_merge(label_str: str, key_field: str, prop_keys: List[str]) -> str:
    """UNWIND 기반 MERGE — CASE WHEN 가드로 빈/NULL 값일 때 기존 prop 보존.

    label_str: ``"FUNCTION"`` 또는 ``"FUNCTION:METHOD"`` (caller 가 sub_label 결정).
    prop_keys: row.<key> 로 SET 할 속성 이름들. 각 prop 마다
    ``n.X = CASE WHEN row.X IS NOT NULL AND trim(toString(row.X)) <> '' THEN row.X ELSE n.X END``.
    """
    set_parts = [
        f"n.{p} = CASE WHEN row.{p} IS NOT NULL AND trim(toString(row.{p})) <> '' "
        f"THEN row.{p} ELSE n.{p} END"
        for p in prop_keys
    ]
    set_clause = ", ".join(set_parts)
    head = (
        f"UNWIND $rows AS row\n"
        f"MERGE (n:{label_str} {{{key_field}: row.{key_field}}})\n"
    )
    if set_clause:
        return (
            head
            + f"ON CREATE SET {set_clause}, n.created_at = timestamp()\n"
            + f"ON MATCH SET {set_clause}, n.updated_at = timestamp()"
        )
    return (
        head
        + "ON CREATE SET n.created_at = timestamp()\n"
        + "ON MATCH SET n.updated_at = timestamp()"
    )


def _emit_node_merges(
    grouped: Dict[str, List[Dict[str, Any]]],
    key_field: str,
    prop_keys: List[str],
) -> List[Dict[str, Any]]:
    """label_str 별 그룹 → UNWIND 쿼리 1개씩 emit."""
    queries: List[Dict[str, Any]] = []
    for label_str, rows in grouped.items():
        if not rows:
            continue
        queries.append({
            "query":  _unwind_node_merge(label_str, key_field, prop_keys),
            "params": {"rows": rows},
        })
    return queries


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Specific query builders
# ═══════════════════════════════════════════════════════════════════════════

# 모듈 레벨 상수 (시스템/더미 테이블 제외, 코드 길이 제한)
_EXCLUDED_TABLE_NAMES = EXCLUDED_TABLE_NAMES
_ARTIFACT_CODE_MAX = ARTIFACT_CODE_MAX
_CHUNK_CODE_MAX = CODE_TEXT_MAX
_SQL_TEXT_MAX = SQL_TEXT_MAX


# ═══════════════════════════════════════════════════════════════════════════
# 2-1. Helpers — FQN 파싱, 이름 추출 등 순수 유틸
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
    """Column FQN 생성. schema 빈값이면 ``table.column`` (강제 'public' 박지 X)."""
    s = (schema or "").strip()
    return f"{s}.{table}.{column}" if s else f"{table}.{column}"


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
# 2-2. Node MERGE — 개별 노드 생성
# ═══════════════════════════════════════════════════════════════════════════

# --- 2-2a. 코드 엔티티 노드 (FUNCTION, MODULE, PACKAGE, VARIABLE) ---

_FUNCTION_PROP_KEYS = [
    "signature", "start_line", "end_line", "code_ref", "module_key", "package_name",
    "module_name", "name", "code_text", "file_path", "directory", "file_name",
    "annotations", "comment",
]


def build_function_queries(function_facts_list: List[FunctionFacts], node_kinds: Dict[str, str]) -> List[Dict[str, Any]]:
    """FUNCTION 노드 MERGE — UNWIND, sub_label 그룹 (FUNCTION:METHOD / FUNCTION:DEFINE / 등).

    Args:
        node_kinds: function_id → AST 타입 (METHOD/DEFINE/...) 매핑. label 의 sub-label.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for m in function_facts_list:
        module_fqn, function_name = _module_fqn_and_function_name(m.fqn)
        module_name_short = _extract_short_name(module_fqn)
        kind = node_kinds.get(m.function_id, "METHOD")
        # code_ref 에서 file_path 추출 (예: "src/core/db_engine.c:575-620" → "src/core/db_engine.c")
        code_ref = m.code_ref
        file_path = code_ref.split(":")[0] if ":" in code_ref else code_ref
        directory, file_name = _split_file_path(file_path)
        label_str = _label_str("FUNCTION", kind)
        grouped.setdefault(label_str, []).append({
            "function_id":  m.function_id,
            "signature":    m.signature,
            "start_line":   m.start_line, "end_line": m.end_line,
            "code_ref":     code_ref, "module_key": m.module_key,
            "package_name": m.package_name,
            "module_name":  module_name_short, "name": function_name,
            "code_text":    m.code[:_CHUNK_CODE_MAX],
            "file_path":    file_path, "directory": directory, "file_name": file_name,
            "annotations":  m.annotations, "comment": m.comment,
        })
    return _emit_node_merges(grouped, "function_id", _FUNCTION_PROP_KEYS)


_CONTAINER_PROP_KEYS = ["name", "type", "package_name", "annotations", "comment", "display_name", "intent"]


def build_module_queries(module_index: Dict[str, Any], lang: str) -> List[Dict[str, Any]]:
    """MODULE 노드 MERGE — module 역할 (함수를 품을 수 있는 컨테이너) 만.

    Java: CLASS/INTERFACE/ENUM/RECORD · C: FILE · Python: CLASS/FILE · Go: STRUCT/INTERFACE
    """
    return _build_container_queries(module_index, lang, role="module")


def build_datatype_queries(module_index: Dict[str, Any], lang: str) -> List[Dict[str, Any]]:
    """DATATYPE 노드 MERGE — data_type 역할 (함수 못 품는 데이터 타입 컨테이너) 만.

    C: STRUCT/UNION/ENUM (다른 언어는 비어 있음)
    """
    return _build_container_queries(module_index, lang, role="data_type")


def _build_container_queries(
    module_index: Dict[str, Any], lang: str, role: str,
) -> List[Dict[str, Any]]:
    """ontology 의 role 에 해당하는 컨테이너만 필터해서 노드 MERGE 쿼리 생성."""
    onto = get_ontology()
    if role == "module":
        kinds = onto.module_types(lang)
    elif role == "data_type":
        kinds = onto.data_type_types(lang)
    else:
        raise ValueError(f"_build_container_queries: 미지원 role='{role}'")
    if not kinds:
        return []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        if not fqn:
            continue
        kind = info.get("kind", "")
        if kind not in kinds:
            continue
        primary_label = onto.primary_label_for(kind, lang)
        name = info.get("name", "")
        if not name and fqn:
            name = _extract_short_name(fqn)
        label_str = _label_str(primary_label, kind)
        grouped.setdefault(label_str, []).append({
            "fqn":          fqn,
            "name":         name,
            "type":         kind,
            "package_name": info.get("package_name") or "",
            "annotations":  info.get("annotations") or "",
            "comment":      info.get("comment") or "",
            "display_name": info.get("display_name") or "",
            "intent":       info.get("intent") or "",
        })
    return _emit_node_merges(grouped, "fqn", _CONTAINER_PROP_KEYS)


def build_package_queries(package_fqns: List[str]) -> List[Dict[str, Any]]:
    """PACKAGE 노드 MERGE — UNWIND 단일 쿼리."""
    rows = [{"fqn": fqn, "name": _extract_short_name(fqn)} for fqn in package_fqns if fqn]
    if not rows:
        return []
    return [{
        "query":  _unwind_node_merge("PACKAGE", "fqn", ["name"]),
        "params": {"rows": rows},
    }]


_VARIABLE_PROP_KEYS = [
    "name", "module_name", "start_line", "end_line", "variable_type", "code_ref",
    "code_text", "annotations", "comment", "init_value",
]


def build_variable_queries(variable_facts_list: List[VariableFacts], lang: str, node_kinds: Dict[str, str]) -> List[Dict[str, Any]]:
    """VARIABLE/CONSTANT 노드 MERGE — UNWIND, (primary, sub_label) 그룹.

    Args:
        node_kinds: variable_id → AST 타입 (FIELD/DEFINE/...) → sub_label.
        lang: 언어 코드. ontology 가 primary 라벨(VARIABLE/CONSTANT) 결정.
    """
    onto = get_ontology()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for f in variable_facts_list:
        module_name = _extract_short_name(f.module_key).split(":")[0]
        kind = node_kinds.get(f.variable_id, "FIELD")
        primary = onto.primary_label_for(kind, lang)
        label_str = _label_str(primary, kind)
        grouped.setdefault(label_str, []).append({
            "variable_id":   f.variable_id,
            "name":          f.name, "module_name": module_name,
            "start_line":    f.start_line, "end_line": f.end_line,
            "variable_type": f.variable_type, "code_ref": f.code_ref,
            "code_text":     f.code[:_CHUNK_CODE_MAX],
            "annotations":   f.annotations, "comment": f.comment,
            "init_value":    f.init_value,
        })
    return _emit_node_merges(grouped, "variable_id", _VARIABLE_PROP_KEYS)


# --- 2-2b. INIT_BY 관계 (Variable → Variable|Constant 초기화 출처) ---

def build_init_by_queries(edges: List[InitByEdge]) -> List[Dict[str, Any]]:
    """(VARIABLE)-[:INIT_BY]->(VARIABLE|CONSTANT) 초기화 관계를 생성한다.

    변수가 다른 변수/상수 값으로 초기화될 때의 출처 관계.
    UNWIND 배치로 한 번에 처리한다.
    """
    if not edges:
        return []
    rows = [{"vid": e.variable_id, "sid": e.init_source_id} for e in edges]
    return [{
        "query": (
            "UNWIND $rows AS row "
            "MATCH (v {variable_id: row.vid}) WHERE v:VARIABLE OR v:CONSTANT "
            "MATCH (s {variable_id: row.sid}) WHERE s:VARIABLE OR s:CONSTANT "
            "MERGE (v)-[:INIT_BY]->(s)"
        ),
        "params": {"rows": rows},
    }]


def build_column_description_update_queries(
    db: str, schema: str, table_name: str, columns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """LLM table_summary 가 반환한 컬럼별 description 을 :Column 노드에 merge.

    Table description 보강 (build_table_description_enrichment_queries) 과 동일 정책:
    - analyzed_description: 항상 SET (누적 " | ")
    - description: 빈 값일 때만 analyzed_description 으로 채움 (DDL/table_linker 의 1차 결과 보존)
    - description_source: description 비어있다 채워진 경우만 'code' 마킹

    호출자는 ``framework/pipeline/phase_summary.py`` 의 _build_queries 콜백.
    """
    queries: List[Dict[str, Any]] = []
    for col in columns:
        col_name = (col.get("name") or "").strip()
        col_desc = (col.get("description") or "").strip()
        if not col_name or not col_desc:
            continue
        queries.append({
            "query": (
                "MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)\n"
                "WHERE (t.name = $table_name OR t.fqn ENDS WITH $table_name)\n"
                "  AND toUpper(c.name) = toUpper($col_name)\n"
                "SET c.analyzed_description = CASE\n"
                "      WHEN c.analyzed_description IS NULL OR trim(toString(c.analyzed_description)) = ''\n"
                "      THEN $col_desc\n"
                "      ELSE c.analyzed_description + ' | ' + $col_desc\n"
                "    END,\n"
                "    c.description = CASE\n"
                "      WHEN c.description IS NULL OR trim(toString(c.description)) = ''\n"
                "      THEN $col_desc\n"
                "      ELSE c.description\n"
                "    END,\n"
                "    c.description_source = CASE\n"
                "      WHEN c.description_source IS NULL OR c.description_source = ''\n"
                "      THEN 'code'\n"
                "      ELSE c.description_source\n"
                "    END,\n"
                "    c.updated_at = timestamp()\n"
                "RETURN c"
            ),
            "params": {"table_name": table_name, "col_name": col_name, "col_desc": col_desc},
        })
    return queries


def build_var_refs_queries(refs: List[tuple]) -> List[Dict[str, Any]]:
    """(source)-[:REFERENCES]->(VARIABLE|CONSTANT).

    source 는 코드를 가진 어떤 노드든 가능 — 함수 본문(FUNCTION) 또는 변수 선언식(VARIABLE).
    refs: ``[(source_id, variable_id), ...]``.
    """
    if not refs:
        return []
    return [{
        "query": (
            "UNWIND $rows AS row\n"
            "OPTIONAL MATCH (f:FUNCTION {function_id: row.sid})\n"
            "OPTIONAL MATCH (vsrc:VARIABLE {variable_id: row.sid})\n"
            "WITH row, coalesce(f, vsrc) AS src\n"
            "WHERE src IS NOT NULL\n"
            "MATCH (v {variable_id: row.vid}) WHERE v:VARIABLE OR v:CONSTANT\n"
            "MERGE (src)-[:REFERENCES]->(v)"
        ),
        "params": {"rows": [{"sid": sid, "vid": vid} for sid, vid in refs]},
    }]


# --- 2-2c. 비코드 엔티티 노드 (ConfigSection, ArtifactChunk, SqlStatement) ---

_CONFIG_SECTION_PROP_KEYS = ["file_path", "start_line", "end_line", "code_text"]


def build_config_section_queries(config_chunks: List[ConfigSectionChunk]) -> List[Dict[str, Any]]:
    """ConfigSection 노드 MERGE — UNWIND 단일 쿼리."""
    rows = [{
        "config_key_path": c.config_key_path,
        "file_path":       c.file_path,
        "start_line":      c.start_line,
        "end_line":        c.end_line,
        "code_text":       c.raw_snippet[:_ARTIFACT_CODE_MAX],
    } for c in config_chunks]
    if not rows:
        return []
    return [{
        "query":  _unwind_node_merge("CONFIG_SECTION", "config_key_path", _CONFIG_SECTION_PROP_KEYS),
        "params": {"rows": rows},
    }]


_ARTIFACT_CHUNK_PROP_KEYS = [
    "chunk_type", "hint_key", "code_text", "file_path", "directory", "file_name",
    "start_line", "end_line",
]


def build_artifact_chunk_queries(artifact_chunks: List[ArtifactChunk]) -> List[Dict[str, Any]]:
    """ArtifactChunk 노드 MERGE — UNWIND 단일 쿼리."""
    rows: List[Dict[str, Any]] = []
    for c in artifact_chunks:
        src = c.source_path
        directory, file_name = _split_file_path(src)
        rows.append({
            "chunk_id":   c.chunk_id,
            "chunk_type": c.chunk_type,
            "hint_key":   c.hint_key,
            "code_text":  c.text[:_ARTIFACT_CODE_MAX],
            "file_path":  src,
            "directory":  directory,
            "file_name":  file_name,
            "start_line": c.start_line,
            "end_line":   c.end_line,
        })
    if not rows:
        return []
    return [{
        "query":  _unwind_node_merge("ARTIFACT_CHUNK", "chunk_id", _ARTIFACT_CHUNK_PROP_KEYS),
        "params": {"rows": rows},
    }]


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


_SQL_STATEMENT_PROP_KEYS = ["sql_type", "start_line", "end_line", "file_path", "sql_text", "code_text"]


def build_sql_statement_queries(sql_chunks: List[SqlStatementChunk]) -> List[Dict[str, Any]]:
    """SqlStatement 노드 MERGE — UNWIND 단일 쿼리."""
    rows: List[Dict[str, Any]] = []
    for s in sql_chunks:
        sql_text = s.sql_text[:_SQL_TEXT_MAX]
        rows.append({
            "sql_key":    s.sql_key,
            "sql_type":   s.sql_type,
            "start_line": s.start_line,
            "end_line":   s.end_line,
            "file_path":  s.file_path,
            "sql_text":   sql_text,
            "code_text":  sql_text,
        })
    if not rows:
        return []
    return [{
        "query":  _unwind_node_merge("SQL_STATEMENT", "sql_key", _SQL_STATEMENT_PROP_KEYS),
        "params": {"rows": rows},
    }]


# --- 2-2d. DB 노드 (Table, Column) — 복합 키/특수 로직으로 커스텀 쿼리 ---

def build_table_queries_from_reads_writes(
    reads_writes: List[ReadsWritesEdge],
    table_descriptions: Optional[Dict[Tuple[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """Reads_writes에서 등장하는 테이블로 Table 노드 MERGE. DUAL 등 제외.

    schema 결정은 `_resolve_schema` 위계 (LLM 명시 → 빈값).
    description 은 table_descriptions 에서 적용.

    주의: DBMS 전략에서 사용하는 라벨 이름은 `Table`(대소문자 포함)이므로,
    여기서도 동일한 라벨을 사용해야 아티팩트/메서드/SQL과 DB 테이블이 하나의 노드로 합쳐진다.
    """
    seen = set()
    queries = []
    desc_map = table_descriptions or {}
    for rw in reads_writes:
        name = rw.table_name
        name_lower = name.strip().lower()
        if name_lower in _EXCLUDED_TABLE_NAMES:
            continue
        schema = _resolve_schema(rw.table_schema)
        key = (schema, name)
        if key in seen:
            continue
        seen.add(key)
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
            "params": {"db": settings.defaults.db_dialect, "schema": schema, "name": name, "table_id": table_id, "description": description or None},
        })
    return queries


def build_column_queries(
    column_refs: List[ColumnRef],
) -> List[Dict[str, Any]]:
    """Column 노드 MERGE (fqn 기준). DUAL 등 제외.

    schema 결정은 `_resolve_schema` 위계 (LLM 명시 → 빈값).
    """
    seen = set()
    queries = []
    for c in column_refs:
        if c.table_name.strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        _schema = _resolve_schema(c.table_schema)
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
                "dtype": (getattr(c, "dtype", None) or "").strip() or None,
                "description": (getattr(c, "description", None) or "").strip() or None,
                "nullable": getattr(c, "nullable", True),
            },
        })
    return queries


# ═══════════════════════════════════════════════════════════════════════════
# 2-3. Relationship MERGE — 구조·FK·참조 관계 생성
# ═══════════════════════════════════════════════════════════════════════════

# --- 3-1. 패키지·모듈 구조 관계 (BELONGS_TO_PACKAGE, HAS_FUNCTION, HAS_VARIABLE) ---

def build_package_hierarchy_queries(package_fqns: List[str]) -> List[Dict[str, Any]]:
    """(자식 PACKAGE)-[:BELONGS_TO_PACKAGE]->(부모 PACKAGE) — UNWIND 단일 쿼리."""
    expanded = _expand_package_fqns(package_fqns)
    rows = [
        {"fqn": fqn, "parent_fqn": fqn.rsplit(".", 1)[0]}
        for fqn in expanded if "." in fqn
    ]
    if not rows:
        return []
    query = (
        "UNWIND $rows AS row\n"
        "MATCH (a:PACKAGE {fqn: row.fqn})\n"
        "MATCH (b:PACKAGE {fqn: row.parent_fqn})\n"
        "MERGE (a)-[r:BELONGS_TO_PACKAGE]->(b)\n"
        "SET r.updated_at = timestamp()"
    )
    return [{"query": query, "params": {"rows": rows}}]


def build_belongs_to_package_queries(module_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """(MODULE)-[:BELONGS_TO_PACKAGE]->(PACKAGE) — UNWIND 단일 쿼리."""
    rows: List[Dict[str, Any]] = []
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        pkg = info.get("package_name")
        if fqn and pkg:
            rows.append({"module_fqn": fqn, "pkg_fqn": pkg})
    if not rows:
        return []
    query = (
        "UNWIND $rows AS row\n"
        "MATCH (a:MODULE {fqn: row.module_fqn})\n"
        "MATCH (b:PACKAGE {fqn: row.pkg_fqn})\n"
        "MERGE (a)-[r:BELONGS_TO_PACKAGE]->(b)\n"
        "SET r.updated_at = timestamp()"
    )
    return [{"query": query, "params": {"rows": rows}}]


def build_root_package_queries(
    package_fqns: List[str],
    module_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """root PACKAGE 노드 + 최상위 패키지/고아 MODULE 연결 — UNWIND 통합.

    - root PACKAGE 노드 (fqn="root", name="root") 생성
    - 부모가 없는 최상위 패키지 → BELONGS_TO_PACKAGE → root
    - package_name 이 없는 MODULE → BELONGS_TO_PACKAGE → root
    """
    queries: List[Dict[str, Any]] = []

    # 1. root PACKAGE 노드 (1행 UNWIND)
    queries.append({
        "query":  _unwind_node_merge("PACKAGE", "fqn", ["name"]),
        "params": {"rows": [{"fqn": "root", "name": "root"}]},
    })

    # 2. 최상위 패키지 → root
    expanded = set(_expand_package_fqns(package_fqns))
    pkg_rows = [{"fqn": fqn} for fqn in expanded if fqn and fqn != "root" and "." not in fqn]
    if pkg_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:PACKAGE {fqn: row.fqn})\n"
                "MATCH (b:PACKAGE {fqn: 'root'})\n"
                "MERGE (a)-[r:BELONGS_TO_PACKAGE]->(b)\n"
                "SET r.updated_at = timestamp()"
            ),
            "params": {"rows": pkg_rows},
        })

    # 3. 고아 MODULE → root
    mod_rows: List[Dict[str, Any]] = []
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        fqn = info.get("fqn") or info.get("module_key")
        pkg = info.get("package_name")
        if fqn and not pkg:
            mod_rows.append({"fqn": fqn})
    if mod_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:MODULE {fqn: row.fqn})\n"
                "MATCH (b:PACKAGE {fqn: 'root'})\n"
                "MERGE (a)-[r:BELONGS_TO_PACKAGE]->(b)\n"
                "SET r.updated_at = timestamp()"
            ),
            "params": {"rows": mod_rows},
        })

    return queries


def build_has_function_queries(
    function_facts_list: List[FunctionFacts],
    module_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """(MODULE)-[:HAS_FUNCTION]->(FUNCTION) + (PACKAGE)-[:BELONGS_TO_PACKAGE]->(FUNCTION).

    UNWIND 두 그룹 (컨테이너 있음 vs 없음).
    """
    has_func_rows: List[Dict[str, Any]] = []
    pkg_belongs_rows: List[Dict[str, Any]] = []
    for m in function_facts_list:
        if m.module_key:
            has_func_rows.append({"module_fqn": m.module_key, "function_id": m.function_id})
        elif m.package_name:
            pkg_belongs_rows.append({"pkg_fqn": m.package_name, "function_id": m.function_id})

    queries: List[Dict[str, Any]] = []
    if has_func_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:MODULE {fqn: row.module_fqn})\n"
                "MATCH (b:FUNCTION {function_id: row.function_id})\n"
                "MERGE (a)-[r:HAS_FUNCTION]->(b)\n"
                "SET r.updated_at = timestamp()"
            ),
            "params": {"rows": has_func_rows},
        })
    if pkg_belongs_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:PACKAGE {fqn: row.pkg_fqn})\n"
                "MATCH (b:FUNCTION {function_id: row.function_id})\n"
                "MERGE (a)-[r:BELONGS_TO_PACKAGE]->(b)\n"
                "SET r.updated_at = timestamp()"
            ),
            "params": {"rows": pkg_belongs_rows},
        })
    return queries


def build_has_variable_queries(
    variable_facts_list: List[VariableFacts],
    module_index: Dict[str, Any],
    lang: str,
) -> List[Dict[str, Any]]:
    """(MODULE)-[:HAS_VARIABLE]->(VARIABLE) — module 역할 컨테이너 의 변수만."""
    rows = _filter_variable_rows(variable_facts_list, module_index, lang, role="module")
    if not rows:
        return []
    return [{
        "query": (
            "UNWIND $rows AS row\n"
            "MATCH (a:MODULE {fqn: row.module_fqn})\n"
            "MATCH (b:VARIABLE {variable_id: row.variable_id})\n"
            "MERGE (a)-[r:HAS_VARIABLE]->(b)\n"
            "SET r.updated_at = timestamp()"
        ),
        "params": {"rows": rows},
    }]


def build_has_member_queries(
    variable_facts_list: List[VariableFacts],
    module_index: Dict[str, Any],
    lang: str,
) -> List[Dict[str, Any]]:
    """(DATATYPE)-[:HAS_MEMBER]->(VARIABLE) — data_type 역할 컨테이너 의 멤버만."""
    rows = _filter_variable_rows(variable_facts_list, module_index, lang, role="data_type")
    if not rows:
        return []
    return [{
        "query": (
            "UNWIND $rows AS row\n"
            "MATCH (a:DATATYPE {fqn: row.module_fqn})\n"
            "MATCH (b:VARIABLE {variable_id: row.variable_id})\n"
            "MERGE (a)-[r:HAS_MEMBER]->(b)\n"
            "SET r.updated_at = timestamp()"
        ),
        "params": {"rows": rows},
    }]


def _filter_variable_rows(
    variable_facts_list: List[VariableFacts],
    module_index: Dict[str, Any],
    lang: str,
    role: str,
) -> List[Dict[str, Any]]:
    """variable_facts 중 부모 컨테이너 kind 가 ontology 의 role 에 속한 것만 추려서 row 생성."""
    onto = get_ontology()
    if role == "module":
        kinds = onto.module_types(lang)
    elif role == "data_type":
        kinds = onto.data_type_types(lang)
    else:
        raise ValueError(f"_filter_variable_rows: 미지원 role='{role}'")
    if not kinds:
        return []
    parent_kind_by_key: Dict[str, str] = {}
    for info in module_index.values():
        if not isinstance(info, dict):
            continue
        mkey = info.get("module_key")
        kind = info.get("kind", "")
        if mkey and kind:
            parent_kind_by_key[mkey] = kind
    rows: List[Dict[str, Any]] = []
    for f in variable_facts_list:
        if not f.module_key:
            continue
        if parent_kind_by_key.get(f.module_key, "") not in kinds:
            continue
        rows.append({"module_fqn": f.module_key, "variable_id": f.variable_id})
    return rows


# --- 3-2. FK 관계 (FK_TO_COLUMN, FK_TO_TABLE) ---

def build_fk_to_column_queries(fk_edges: List[FkEdge]) -> List[Dict[str, Any]]:
    """(COLUMN)-[:FK_TO_COLUMN]->(COLUMN) 관계. DUAL 등 제외. 대소문자 무시 매칭."""
    if not fk_edges:
        return []
    queries = []
    seen: set = set()
    for e in fk_edges:
        if e.from_table.strip().lower() in _EXCLUDED_TABLE_NAMES or e.to_table.strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        from_col = e.from_column
        to_col = e.to_column
        if not from_col or not to_col:
            continue
        dedup_key = (e.from_table.lower(), from_col.lower(), e.to_table.lower(), to_col.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        from_fqn = _build_column_fqn(e.from_schema, e.from_table, from_col)
        to_fqn = _build_column_fqn(e.to_schema, e.to_table, to_col)
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

def build_has_column_queries(
    column_refs: List[ColumnRef],
) -> List[Dict[str, Any]]:
    """(Table)-[:HAS_COLUMN]->(Column) 관계. DUAL 등 제외. schema 위계 적용."""
    seen = set()
    queries = []
    for c in column_refs:
        if c.table_name.strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        schema = _resolve_schema(c.table_schema)
        name = c.table_name
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
        queries.append({"query": q, "params": {"db": settings.defaults.db_dialect, "schema": schema, "name": name, "fqn": fqn}})
    return queries


def build_fk_to_table_queries(
    fk_edges: List[FkEdge],
) -> List[Dict[str, Any]]:
    """(TABLE)-[:FK_TO_TABLE]->(TABLE) 관계를 테이블쌍당 1개만 생성.

    schema 위계 적용 (LLM 명시 → DDL 매칭 → 빈값).
    관계 속성: source(최초 생성 시만 기록), sourceColumn, targetColumn.
    """
    if not fk_edges:
        return []
    seen: set = set()
    queries = []
    for e in fk_edges:
        from_table = e.from_table
        to_table = e.to_table
        from_schema = _resolve_schema(e.from_schema)
        to_schema = _resolve_schema(e.to_schema)
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
                "db": settings.defaults.db_dialect, "from_schema": from_schema, "from_table": from_table,
                "to_schema": to_schema, "to_table": to_table,
                "source": getattr(e, "source", "code"),
                "from_column": e.from_column, "to_column": e.to_column,
            },
        })
    return queries


# --- 3-4. 데이터 접근 관계 (SELECT/INSERT/UPDATE/DELETE, READS/WRITES, REFER_TO) ---

def build_reads_writes_queries(
    edges: List[ReadsWritesEdge],
) -> List[Dict[str, Any]]:
    """(SqlStatement)-[:SELECT|INSERT|UPDATE|DELETE]->(Table) 관계. DUAL 등 제외. schema 위계 적용."""
    queries = []
    for e in edges:
        name = e.table_name
        schema = _resolve_schema(e.table_schema)
        if (name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        if not e.relation or not e.relation.strip():
            raise ValueError(
                f"build_reads_writes_queries: ReadsWritesEdge.relation 누락 — sql_key={e.sql_key} table={schema}.{name}"
            )
        rel = e.relation.strip()
        if rel not in ("SELECT", "INSERT", "UPDATE", "DELETE", "REFER_TO"):
            raise ValueError(
                f"build_reads_writes_queries: relation='{rel}' 미허용 (SELECT/INSERT/UPDATE/DELETE/REFER_TO 만 허용)"
            )
        q = (
            "MATCH (s:SQL_STATEMENT {sql_key: $sql_key})\n"
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            f"MERGE (s)-[r:{rel}]->(t)\n"
            "SET r.updated_at = timestamp()\n"
            "RETURN r"
        )
        queries.append({"query": q, "params": {"sql_key": e.sql_key, "db": settings.defaults.db_dialect, "schema": schema, "name": name}})
    return queries


def build_node_table_queries(
    edges: List[FunctionTableEdge],
) -> List[Dict[str, Any]]:
    """(Function|Field)-[:READS|WRITES]->(Table) 관계. DUAL 등 제외. schema 위계 적용.

    테이블 매칭은 대소문자 무시(toLower). 기존 Table 노드가 있으면 MATCH로 찾아 연결하고,
    없으면 MERGE로 새로 생성한다 (DeepAgent 시나리오 4 대응).
    """
    queries = []
    for e in edges:
        name = e.table_name
        schema = _resolve_schema(e.table_schema)
        if (name or "").strip().lower() in _EXCLUDED_TABLE_NAMES:
            continue
        if not e.relation or not e.relation.strip():
            raise ValueError(
                f"build_node_table_queries: FunctionTableEdge.relation 누락 — function_id={e.function_id} table={schema}.{name}"
            )
        rel = e.relation
        op = (getattr(e, "op", "") or "").strip().upper()
        use_field = getattr(e, "variable_id", None)
        source_match = "MATCH (f:VARIABLE {variable_id: $variable_id})\n" if use_field else "MATCH (m:FUNCTION {function_id: $function_id})\n"
        source_node = "f" if use_field else "m"
        table_id = f"{schema}.{name}" if schema else name
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
            + f"MERGE ({source_node})-[r:{rel} {{op: $op}}]->(t)\n"
            + "SET r.updated_at = timestamp()\n"
            + "RETURN r"
        )
        params: Dict[str, Any] = {
            "function_id": e.function_id, "db": settings.defaults.db_dialect,
            "schema": schema, "name": name, "table_id": table_id, "op": op,
        }
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

    schema 결정: schema_default (호출자 명시) > "" (모름).

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
        schema = _resolve_schema(schema_default)

        # 멤버변수(VariableFacts)에서 Column 정보 추출
        member_vars = vars_by_module.get(module_key, [])
        for v in member_vars:
            if not v.name:
                continue
            column_refs.append(ColumnRef(
                table_schema=schema,
                table_name=table_name,
                column_name=v.name,
                dtype=v.variable_type,
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
        schema = _resolve_schema(schema_default)
        table_id = f"{schema}.{table_name}" if schema else table_name
        q = (
            "MERGE (n:Table {db: $db, schema: $schema, name: $name})\n"
            "ON CREATE SET n.created_at = timestamp(), n.table_id = $table_id\n"
            "ON MATCH SET n.updated_at = timestamp()\n"
            "RETURN n"
        )
        queries.append({
            "query": q,
            "params": {"db": settings.defaults.db_dialect, "schema": schema, "name": table_name, "table_id": table_id},
        })

    # Column 노드 MERGE + HAS_COLUMN 관계
    queries.extend(build_column_queries(column_refs))
    queries.extend(build_has_column_queries(column_refs))

    return queries


def build_module_table_refer_to_queries(
    edges: List[ModuleTableEdge],
) -> List[Dict[str, Any]]:
    """엔티티 (MODULE)-[:REFER_TO]->(TABLE) 출처 연결. DUAL 등 제외."""
    queries = []
    for e in edges:
        name = e.table_name.strip()
        schema = _resolve_schema(e.table_schema)
        if (name or "").lower() in _EXCLUDED_TABLE_NAMES:
            continue
        fqn = e.module_fqn.strip()
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
            "params": {"module_fqn": fqn, "db": settings.defaults.db_dialect, "schema": schema, "name": name},
        })
    return queries


def build_artifact_table_refer_to_queries(
    edges: List[ArtifactTableEdge],
) -> List[Dict[str, Any]]:
    """EJB XML (ARTIFACT_CHUNK)-[:REFER_TO]->(TABLE). entity_xml:: 전용."""
    queries = []
    for e in edges:
        name = e.table_name.strip()
        schema = _resolve_schema(e.table_schema)
        if (name or "").lower() in _EXCLUDED_TABLE_NAMES:
            continue
        chunk_id = e.chunk_id.strip()
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
            "params": {"chunk_id": chunk_id, "db": settings.defaults.db_dialect, "schema": schema, "name": name},
        })
    return queries


# --- 3-5. 범용 참조 관계 (CALLS_SQL, USES_CONFIG, REFER_TO) ---

def build_refer_to_queries(edges: List[ReferToEdge]) -> List[Dict[str, Any]]:
    """(Method|Field)-[:CALLS_SQL|USES_CONFIG|REFER_TO]->(SqlStatement|ConfigSection|ArtifactChunk|Class)."""
    queries = []
    for e in edges:
        if not e.target_type or not e.target_type.strip():
            raise ValueError(
                f"build_refer_to_queries: ReferToEdge.target_type 누락 — function_id={e.function_id} target_id={e.target_id}"
            )
        target_type = e.target_type.strip()
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
            raise ValueError(
                f"build_refer_to_queries: 지원하지 않는 target_type='{target_type}' "
                "(SqlStatement | ConfigSection | ArtifactChunk | Module 만 허용)"
            )
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
# 2-4. Summary / Label — summary·stereotype 속성 및 라벨 업데이트
# ═══════════════════════════════════════════════════════════════════════════

def build_analysis_result_queries(
    analysis_by_function: Dict[str, Dict[str, Any]],
    lang: str,
) -> List[Dict[str, Any]]:
    """FUNCTION 요약/라벨 업데이트 + Example Mapping 3요소(Rule/Example/Question) 생성.

    흐름:
      (a) 함수별 데이터를 수집해 rows/edges 로 평탄화
      (b) UNWIND Cypher 쿼리로 조립
    """
    valid_for_lang = get_ontology().valid_stereotypes(lang)

    # ─────────────────────────────────────────────────────────────
    # (a) 수집 — 함수별로 rows 를 평탄화
    # ─────────────────────────────────────────────────────────────
    function_rows: List[Dict[str, Any]] = []
    rule_rows: List[Dict[str, Any]] = []
    example_rows: List[Dict[str, Any]] = []
    example_table_edges: List[Dict[str, Any]] = []
    question_rows: List[Dict[str, Any]] = []
    next_edges: List[Dict[str, Any]] = []
    branch_edges: List[Dict[str, Any]] = []
    stereotype_groups: Dict[str, List[str]] = {}

    for fid, data in analysis_by_function.items():
        # FUNCTION 속성
        stereotype = (data.get("stereotype") or "").strip()
        if stereotype not in valid_for_lang:
            stereotype = ""
        function_rows.append({
            "function_id": fid,
            "stereotype": stereotype,
            "summary": (data.get("summary") or "").strip(),
            "actor": (data.get("actor") or "").strip() or None,
        })
        if stereotype:
            stereotype_groups.setdefault(stereotype, []).append(fid)

        # Rule 수집 (같은 함수 내 rule_id 로 Example·flow 를 다시 참조)
        flow_by_rid: Dict[str, str] = {}
        branch_by_rid: Dict[str, Optional[str]] = {}
        for r in (data.get("rules") or []):
            if not isinstance(r, dict):
                continue
            rid = str(r.get("rule_id", "")).strip()
            statement = (r.get("statement") or "").strip()
            if not rid or not statement:
                continue
            flow_id = str(r.get("flow_id", "")).strip() or rid
            branch_from = r.get("branch_from") or None
            flow_by_rid[rid] = flow_id
            branch_by_rid[rid] = branch_from
            rule_rows.append({
                "function_id": fid,
                "rule_id": rid,
                "rule_hash": rule_hash(statement),
                "statement": statement,
                "flow_id": flow_id,
                "guard_rule_id": r.get("guard_rule_id") or None,
                "branch_from": branch_from,
                "coupled_domains": [str(d).strip() for d in (r.get("coupled_domains") or []) if str(d).strip()],
            })

        # Example 수집 (given/then dict→JSON, then.writes[]/then.reads[] → AFFECTS_TABLE 엣지)
        rows, writes = collect_example_rows(
            data.get("examples") or [],
            valid_rule_ids=flow_by_rid.keys(),
            example_id_factory=lambda rid, idx: f"{fid}::{rid}::{idx}",
        )
        for r in rows:
            r["function_id"] = fid
            example_rows.append(r)
        example_table_edges.extend(writes)

        # Question 수집
        for idx, q in enumerate(data.get("questions") or [], start=1):
            if not isinstance(q, dict):
                continue
            text = (q.get("text") or "").strip()
            if not text:
                continue
            question_rows.append({
                "function_id": fid,
                "question_id": f"{fid}::Q{idx}",
                "text": text,
                "reason": (q.get("reason") or "").strip(),
            })

        # Rule 간 흐름 계산 (메인 NEXT + 분기 BRANCH)
        main_rids = sorted(
            (rid for rid, flow in flow_by_rid.items() if "-" not in flow),
            key=lambda rid: flow_by_rid[rid],
        )
        for i in range(len(main_rids) - 1):
            next_edges.append({
                "function_id": fid,
                "from_rule_id": main_rids[i],
                "to_rule_id": main_rids[i + 1],
            })
        flow_to_rid = {flow: rid for rid, flow in flow_by_rid.items()}
        for rid, flow in flow_by_rid.items():
            if "-" not in flow:
                continue
            parent_rid = branch_by_rid.get(rid) or flow_to_rid.get(flow.rsplit("-", 1)[0])
            if parent_rid and parent_rid in flow_by_rid:
                branch_edges.append({
                    "function_id": fid,
                    "from_rule_id": parent_rid,
                    "to_rule_id": rid,
                })

    # ─────────────────────────────────────────────────────────────
    # (b) 쿼리 조립
    # ─────────────────────────────────────────────────────────────
    queries: List[Dict[str, Any]] = []

    # FUNCTION 요약·stereotype 속성
    if function_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (n:FUNCTION {function_id: row.function_id})\n"
                "SET n.updated_at = timestamp(), n.stereotype = row.stereotype, n.summary = row.summary\n"
                "RETURN n"
            ),
            "params": {"rows": function_rows},
        })

    # stereotype 라벨 (언어별 허용 범위만)
    for label, ids in stereotype_groups.items():
        queries.append({
            "query": f"UNWIND $ids AS mid\nMATCH (n:FUNCTION {{function_id: mid}})\nSET n:{label}\nRETURN n",
            "params": {"ids": ids},
        })

    # Rule 노드 (rule_hash 로 함수 간 공유) + HAS_RULE 관계 속성
    if rule_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MERGE (r:Rule {rule_hash: row.rule_hash})\n"
                "  ON CREATE SET r.statement = row.statement, r.created_at = timestamp()\n"
                "WITH r, row\n"
                "MATCH (f:FUNCTION {function_id: row.function_id})\n"
                "MERGE (f)-[rel:HAS_RULE {local_id: row.rule_id}]->(r)\n"
                "SET rel.flow_id = row.flow_id,\n"
                "    rel.guard_rule_id = row.guard_rule_id,\n"
                "    rel.branch_from = row.branch_from,\n"
                "    rel.coupled_domains = row.coupled_domains,\n"
                "    rel.updated_at = timestamp()\n"
                "RETURN f, rel, r"
            ),
            "params": {"rows": rule_rows},
        })

    # Example 노드 + HAS_EXAMPLE 관계
    # given/then 은 하이브리드 JSON 직렬화 문자열 (apoc.convert.fromJsonMap 으로 역직렬화).
    if example_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (f:FUNCTION {function_id: row.function_id})-[:HAS_RULE {local_id: row.rule_id}]->(r:Rule)\n"
                "MERGE (e:Example {example_id: row.example_id})\n"
                "SET e.description = row.description,\n"
                "    e.given = row.given, e.when_ = row.when_, e.then_ = row.then_,\n"
                "    e.is_boundary = row.is_boundary, e.updated_at = timestamp()\n"
                "MERGE (r)-[:HAS_EXAMPLE]->(e)\n"
                "RETURN r, e"
            ),
            "params": {"rows": example_rows},
        })

    # Example → Table AFFECTS_TABLE 엣지 (then.writes[] + then.reads[] 로부터, op 으로 구분)
    if example_table_edges:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (e:Example {example_id: row.example_id})\n"
                "MATCH (t:Table) WHERE toLower(t.name) = toLower(row.table_name)\n"
                "MERGE (e)-[rel:AFFECTS_TABLE {op: row.op}]->(t)\n"
                "SET rel.updated_at = timestamp()\n"
                "RETURN e, rel, t"
            ),
            "params": {"rows": example_table_edges},
        })

    # Question 노드 + HAS_QUESTION 관계
    if question_rows:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (f:FUNCTION {function_id: row.function_id})\n"
                "MERGE (q:Question {question_id: row.question_id})\n"
                "  ON CREATE SET q.text = row.text, q.reason = row.reason, q.created_at = timestamp()\n"
                "  ON MATCH  SET q.text = row.text, q.reason = row.reason, q.updated_at = timestamp()\n"
                "MERGE (f)-[:HAS_QUESTION]->(q)\n"
                "RETURN f, q"
            ),
            "params": {"rows": question_rows},
        })

    # Rule 간 NEXT(메인 흐름) / BRANCH(분기) 관계
    for rel_type, edges in (("NEXT", next_edges), ("BRANCH", branch_edges)):
        if edges:
            queries.append({
                "query": (
                    f"UNWIND $rows AS row\n"
                    f"MATCH (f:FUNCTION {{function_id: row.function_id}})\n"
                    f"MATCH (f)-[:HAS_RULE {{local_id: row.from_rule_id}}]->(a:Rule)\n"
                    f"MATCH (f)-[:HAS_RULE {{local_id: row.to_rule_id}}]->(b:Rule)\n"
                    f"MERGE (a)-[:{rel_type} {{function_id: row.function_id}}]->(b)"
                ),
                "params": {"rows": edges},
            })

    return queries


def build_actor_queries(analysis_by_function: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Actor 노드 + ROLE 관계 생성 쿼리. 동의어 통합 후 호출."""
    actor_rows = []
    for function_id, data in analysis_by_function.items():
        actor = (data.get("actor") or "").strip()
        if actor:
            actor_rows.append({"function_id": function_id, "actor": actor})
    if not actor_rows:
        return []
    return [{
        "query": (
            "UNWIND $rows AS row\n"
            "MATCH (f:FUNCTION {function_id: row.function_id})\n"
            "MERGE (a:Actor {name: row.actor})\n"
            "ON CREATE SET a.created_at = timestamp()\n"
            "MERGE (a)-[r:ROLE]->(f)\n"
            "ON CREATE SET r.created_at = timestamp()\n"
            "RETURN a, f"
        ),
        "params": {"rows": actor_rows},
    }]


def build_variable_summary_queries(summary_by_variable: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """VARIABLE 노드에 summary 속성 업데이트. UNWIND 배치.

    NOTE: 현재 호출 안 됨 — Field 분석은 `phase_summary.run_module_step` 안에서
    비활성 상태(`summary_by_variable: dict = {}` + tracker step end). 활성화 시 본 함수가 사용됨.
    """
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
# 2-5. CALLS / USES — 호출·의존 관계 (UNWIND 배치)
# ═══════════════════════════════════════════════════════════════════════════

def build_calls_queries(edges: List[CallsEdge], lang: str) -> List[Dict[str, Any]]:
    """(FUNCTION)-[:CALLS]->(FUNCTION). UNWIND 배치: internal/external 2개 쿼리."""
    internal_rows = []
    external_rows = []
    for e in edges:
        scope = e.scope
        caller_cfqn, caller_mname = _module_fqn_and_function_name(e.caller_function_id)
        callee_cfqn, callee_mname = _module_fqn_and_function_name(e.callee_function_id)
        caller_module_name = _extract_short_name(caller_cfqn)
        callee_module_name = _extract_short_name(callee_cfqn)

        if scope == "external" or e.callee_function_id.startswith("external:"):
            if e.callee_function_id.startswith("external:"):
                callee_module_name_ext, callee_function_name_ext = _parse_external_function_id(e.callee_function_id)
            else:
                callee_module_name_ext = callee_module_name
                callee_function_name_ext = callee_mname
            external_rows.append({
                "caller_module_name": caller_module_name, "caller_function_name": caller_mname,
                "callee_function_id": e.callee_function_id,
                "callee_module_name": callee_module_name_ext, "callee_function_name": callee_function_name_ext,
                "call_code": e.call_code[:200],
            })
        else:
            internal_rows.append({
                "caller_module_name": caller_module_name, "caller_function_name": caller_mname,
                "callee_module_name": callee_module_name, "callee_function_name": callee_mname,
                "call_code": e.call_code[:200],
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
        ext_mod_label = _external_module_label_str(lang)
        ext_fn_label = _external_function_label_str(lang)
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:FUNCTION)\n"
                "WHERE a.module_name = row.caller_module_name AND a.name = row.caller_function_name\n"
                f"MERGE (ec:{ext_mod_label} {{name: row.callee_module_name}})\n"
                "ON CREATE SET ec.created_at = timestamp()\n"
                "ON MATCH SET ec.updated_at = timestamp()\n"
                f"MERGE (b:{ext_fn_label} {{function_id: row.callee_function_id}})\n"
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


def build_uses_queries(edges: List[UsesEdge], lang: str) -> List[Dict[str, Any]]:
    """(MODULE)-[:USES]->(MODULE). UNWIND 배치: internal/external 2개 쿼리."""
    internal_rows = []
    external_rows = []
    for e in edges:
        from_module_name = _extract_short_name(e.from_fqn)
        to_module_name = _extract_short_name(e.to_fqn)
        if not from_module_name or not to_module_name or from_module_name == to_module_name:
            continue
        scope = e.scope
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
        ext_mod_label = _external_module_label_str(lang)
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                "MATCH (a:MODULE)\n"
                "WHERE a.name = row.from_module_name\n"
                f"MERGE (b:{ext_mod_label} {{name: row.to_module_name}})\n"
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
# 2-6. UML — 상속(EXTENDS/IMPLEMENTS), 연관(ASSOCIATION), 의존/합성/집합
# ═══════════════════════════════════════════════════════════════════════════

def build_uml_dependency_queries(
    analysis_by_function: Dict[str, Dict[str, Any]],
    function_facts_list: List[Any],
    module_index: Dict[str, Any],
    lang: str,
) -> List[Dict[str, Any]]:
    """Step6 analysis_by_function의 dependencies로 DEPENDENCY/COMPOSITION/AGGREGATION 쿼리 생성.

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
    for function_id, data in (analysis_by_function or {}).items():
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
            rel_type_raw = (dep.get("relationType") or "").strip().upper()
            if not rel_type_raw or rel_type_raw not in ("DEPENDENCY", "COMPOSITION", "AGGREGATION", "ASSOCIATION"):
                # LLM 응답 항목 단위 환각 방어 — drop + errlog (전체 분석은 진행)
                logger.error(
                    "[UML_DEPS] dependency 항목 drop — relationType 누락 또는 미허용 | "
                    "function_id=%s target=%s relationType=%r",
                    function_id, target_module, dep.get("relationType"),
                )
                continue  # noqa: SILENT_FAIL — LLM 환각 항목은 전체 batch 를 막지 않도록 항목 drop
            rel_type = rel_type_raw
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
            ext_mod_label = _external_module_label_str(lang)
            if rel_type == "DEPENDENCY":
                q = (
                    "UNWIND $rows AS row\n"
                    "MATCH (src:MODULE {fqn: row.source_fqn})\n"
                    f"MERGE (t:{ext_mod_label} {{name: row.target_module_name}})\n"
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
                    f"MERGE (t:{ext_mod_label} {{name: row.target_module_name}})\n"
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
# 2-7. Table Description 보강 — 메서드 summary 기반 테이블 설명 생성
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
    """테이블 description 보강 쿼리 빌드 (DBMS strategy 와 동일 schema).

    description = DDL 코멘트 / table_linker SQL 추출 결과. 비어있을 때만 강화 결과로 채움 (fallback).
    analyzed_description = phase_summary 가 모든 함수 사용 패턴 종합한 LLM 결과. 항상 SET.

    enrichments: [{db, schema, name, analyzed_description}, ...]
    """
    queries = []
    for item in enrichments:
        q = (
            "MATCH (t:Table {db: $db, schema: $schema, name: $name})\n"
            "SET t.analyzed_description = $analyzed_description,\n"
            "    t.description = CASE\n"
            "      WHEN t.description IS NULL OR trim(toString(t.description)) = ''\n"
            "      THEN $analyzed_description\n"
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
                "analyzed_description": item.get("analyzed_description", ""),
            },
        })
    return queries
