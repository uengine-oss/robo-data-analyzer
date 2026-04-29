"""DBMS strategy 의 Cypher 빌더 모음.

Framework 의 `framework/common/graph_queries.py` 와 동일한 책임 — DBMS 측 Neo4j
쿼리 빌더를 한 곳에 모은다. 이전에는 `phase_summary.py` (647줄) 와
`analysis/ast_analyzer.py` 안에 산재해 있었다.

EM (Rule/Example/Question) 빌더는 양쪽 strategy 가 공유하는
`analyzer/common/cypher/example_mapping.py` 의 `build_em_queries` 를
DBMS anchor 형태로 wrapping 해서 호출.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from analyzer.common.pipeline.example_mapping import EmBatch, build_em_queries


# ═══════════════════════════════════════════════════════════════════════════
# Procedure anchor — DBMS 의 PROCEDURE/FUNCTION/TRIGGER 노드 composite key
# ═══════════════════════════════════════════════════════════════════════════


def _proc_anchor_match(procedure_type: str, has_schema: bool) -> str:
    """anchor MATCH 절 — schema 유무에 따라 composite key 가 다름.

    schema 있음: ``MATCH (a:{type} {schema_name, procedure_name, directory, file_name})``
    schema 없음: ``MATCH (a:{type} {procedure_name, directory, file_name})``
    """
    if has_schema:
        return (
            f"MATCH (a:{procedure_type} "
            "{schema_name: row.schema_name, procedure_name: row.procedure_name, "
            "directory: row.directory, file_name: row.file_name})"
        )
    return (
        f"MATCH (a:{procedure_type} "
        "{procedure_name: row.procedure_name, directory: row.directory, "
        "file_name: row.file_name})"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Procedure summary + EM 통합 빌더
# ═══════════════════════════════════════════════════════════════════════════


def build_procedure_summary_queries(
    *,
    procedure_type: str,
    procedure_name: str,
    schema_name: str,
    directory: str,
    file_name: str,
    summary: str,
    analysis: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """프로시저 1개 단위의 Summary + Example Mapping Cypher 쿼리 리스트.

    흐름:
      1. PROCEDURE.summary SET
      2. EM (Rule/Example/Question) Cypher — `analyzer/common/cypher/example_mapping`
         의 공용 빌더 호출

    Args:
        procedure_type: PROCEDURE | FUNCTION | TRIGGER
        procedure_name, schema_name, directory, file_name: anchor composite key
        summary: 프로시저 요약 텍스트
        analysis: ``{"rules": [...], "examples": [...], "questions": [...]}``
    """
    has_schema = bool(schema_name)
    anchor_match = _proc_anchor_match(procedure_type, has_schema)

    # anchor 키 — schema 유무에 따라 다름
    anchor_keys: Dict[str, Any] = {
        "procedure_name": procedure_name,
        "directory": directory,
        "file_name": file_name,
    }
    if has_schema:
        anchor_keys["schema_name"] = schema_name

    proc_id = f"{directory}/{file_name}:{procedure_name}"

    queries: List[Dict[str, Any]] = []

    # 1) PROCEDURE.summary SET — UNWIND 단일 row 로 EM 빌더와 동일한 row.xxx 표기 통일
    queries.append({
        "query": (
            "UNWIND $rows AS row\n"
            f"{anchor_match}\n"
            "SET a.summary = row.summary\n"
            "RETURN a"
        ),
        "params": {"rows": [{**anchor_keys, "summary": str(summary)}]},
    })

    # 2) EM Cypher — 공용 빌더
    em_batch = EmBatch(
        em_node_id=proc_id,
        anchor_keys=anchor_keys,
        rules=analysis.get("rules") or [],
        examples=analysis.get("examples") or [],
        questions=analysis.get("questions") or [],
    )
    queries.extend(
        build_em_queries(
            anchor_match=anchor_match,
            batches=[em_batch],
            flow_rel_id_field="procedure_id",
        )
    )

    return queries


# ═══════════════════════════════════════════════════════════════════════════
# Table / Column description 보강 쿼리 — phase_summary 의 LLM 결과 적용용
# ═══════════════════════════════════════════════════════════════════════════


TABLE_DESCRIPTION_UPDATE_QUERY = (
    "MATCH (__cy_t__:Table {schema: $schema, name: $name, db: $db}) "
    "SET __cy_t__.analyzed_description = $llm_desc "
    "WITH __cy_t__ "
    "WHERE __cy_t__.description IS NULL OR __cy_t__.description = '' "
    "SET __cy_t__.description = $llm_desc, __cy_t__.description_source = 'code' "
    "RETURN __cy_t__"
)

COLUMN_DESCRIPTION_UPDATE_QUERY = (
    "MATCH (__cy_c__:Column {fqn: $fqn}) "
    "SET __cy_c__.analyzed_description = $llm_desc "
    "WITH __cy_c__ "
    "WHERE __cy_c__.description IS NULL OR __cy_c__.description = '' "
    "SET __cy_c__.description = $llm_desc, __cy_c__.description_source = 'code' "
    "RETURN __cy_c__"
)


def build_table_description_query(
    *, schema: str, name: str, db: str, llm_desc: str
) -> Dict[str, Any]:
    """:Table 노드의 설명(description/analyzed_description) 보강 쿼리."""
    return {
        "query": TABLE_DESCRIPTION_UPDATE_QUERY,
        "params": {"schema": schema, "name": name, "db": db, "llm_desc": llm_desc},
    }


def build_column_description_query(
    *, datasource: str, schema: str, table_name: str, column_name: str, llm_desc: str
) -> Dict[str, Any]:
    """:Column 노드의 설명(description/analyzed_description) 보강 쿼리.

    fqn 은 lower case 의 ``datasource.schema.table.column`` 4-part 형식
    (DDL handler 와 동일 규칙 — datasource 빠지면 MATCH 실패).
    """
    fqn = ".".join(filter(None, [datasource, schema, table_name, column_name])).lower()
    return {
        "query": COLUMN_DESCRIPTION_UPDATE_QUERY,
        "params": {"fqn": fqn, "llm_desc": llm_desc},
    }


# ═══════════════════════════════════════════════════════════════════════════
# AST 정적 그래프용 빌더 — Package, HAS_PROCEDURE, PARENT_OF, NEXT, Table merge
# (이전: dbms/analysis/ast_analyzer.py 의 _build_* 메서드들)
# ═══════════════════════════════════════════════════════════════════════════


def build_package_queries(*, directory: str, full_directory: str, file_name: str) -> List[Dict[str, Any]]:
    """Package 노드 + FILE-[BELONGS_TO_PACKAGE]->Package 관계.

    Args:
        directory: package 이름 (폴더명 — 예: "RWIS")
        full_directory: FILE 노드의 directory 속성 (예: "RWIS/auth.sql")
        file_name: FILE 노드의 file_name 속성
    """
    if not directory:
        return []
    return [
        {
            "query": "MERGE (__cy_pkg__:PACKAGE {name: $package_name})\nRETURN __cy_pkg__",
            "params": {"package_name": directory},
        },
        {
            "query": (
                "MATCH (__cy_f__:FILE {directory: $directory, file_name: $file_name})\n"
                "MATCH (__cy_pkg__:PACKAGE {name: $package_name})\n"
                "MERGE (__cy_f__)-[__cy_r__:BELONGS_TO_PACKAGE]->(__cy_pkg__)\n"
                "RETURN __cy_f__, __cy_pkg__, __cy_r__"
            ),
            "params": {
                "directory": full_directory,
                "file_name": file_name,
                "package_name": directory,
            },
        },
    ]


def build_has_procedure_query(
    *,
    parent_type: str, parent_line: int,
    child_type: str, child_line: int,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """(FILE)-[:HAS_PROCEDURE]->(PROCEDURE/FUNCTION) 관계 쿼리."""
    return {
        "query": (
            f"MATCH (__cy_p__:{parent_type} {{startLine: $parent_line, directory: $directory, file_name: $file_name}})\n"
            f"MATCH (__cy_c__:{child_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
            "MERGE (__cy_p__)-[__cy_r__:HAS_PROCEDURE]->(__cy_c__)\n"
            "RETURN __cy_r__"
        ),
        "params": {
            "parent_line": parent_line, "child_line": child_line,
            "directory": full_directory, "file_name": file_name,
        },
    }


def build_parent_of_query(
    *,
    parent_type: str, parent_line: int,
    child_type: str, child_line: int,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """(parent)-[:PARENT_OF]->(child) 관계 쿼리."""
    return {
        "query": (
            f"MATCH (__cy_p__:{parent_type} {{startLine: $parent_line, directory: $directory, file_name: $file_name}})\n"
            f"MATCH (__cy_c__:{child_type} {{startLine: $child_line, directory: $directory, file_name: $file_name}})\n"
            "MERGE (__cy_p__)-[__cy_r__:PARENT_OF]->(__cy_c__)\n"
            "RETURN __cy_r__"
        ),
        "params": {
            "parent_line": parent_line, "child_line": child_line,
            "directory": full_directory, "file_name": file_name,
        },
    }


def build_next_query(
    *,
    prev_type: str, prev_line: int,
    curr_type: str, curr_line: int,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """(prev)-[:NEXT]->(curr) 관계 쿼리 (형제 노드 순서)."""
    return {
        "query": (
            f"MATCH (__cy_prev__:{prev_type} {{startLine: $prev_line, directory: $directory, file_name: $file_name}})\n"
            f"MATCH (__cy_curr__:{curr_type} {{startLine: $curr_line, directory: $directory, file_name: $file_name}})\n"
            "MERGE (__cy_prev__)-[__cy_r__:NEXT]->(__cy_curr__)\n"
            "RETURN __cy_r__"
        ),
        "params": {
            "prev_line": prev_line, "curr_line": curr_line,
            "directory": full_directory, "file_name": file_name,
        },
    }


def build_table_merge(
    *,
    table_name: str,           # name_case 변환 후
    effective_schema: str,     # name_case 변환 후
    db: str,
    preserve_vars: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """:Table + :Schema 노드 MERGE + BELONGS_TO 관계.

    Returns dict 에 ``effective_schema`` / ``converted_table_name`` 키도 포함 — 호출자가
    후속 쿼리에서 같은 키 값을 재사용하기 편하도록.

    Args:
        preserve_vars: WITH 절에 같이 끌고갈 변수 이름들 (예: ``["__cy_n__"]``).
    """
    if preserve_vars:
        with_vars = ", ".join(list(preserve_vars) + ["__cy_s__"])
    else:
        with_vars = "__cy_s__"
    return {
        "query": (
            "MERGE (__cy_s__:Schema {db: $db, name: $schema_name})\n"
            f"WITH {with_vars}\n"
            "MERGE (__cy_t__:Table {name: $table_name, schema: $schema_name, db: $db})\n"
            "MERGE (__cy_t__)-[:BELONGS_TO]->(__cy_s__)"
        ),
        "params": {
            "db": db,
            "schema_name": effective_schema,
            "table_name": table_name,
        },
        "effective_schema": effective_schema,
        "converted_table_name": table_name,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CALLS / REFERENCES — 결정론 링킹 (call_linker / variable_linker 위임용)
# ═══════════════════════════════════════════════════════════════════════════


def build_calls_external_pkg_query(
    *,
    src_node_type: str, src_line: int,
    pkg: str, proc_name: str,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """``PKG.FUNC()`` 형태의 외부 패키지 호출 → CALLS {scope: 'external'}."""
    return {
        "query": (
            f"MATCH (src:{src_node_type} {{startLine: $src_line, directory: $directory, file_name: $file_name}})\n"
            "MERGE (tgt:PROCEDURE {directory: $pkg_dir, procedure_name: $proc_name})\n"
            "MERGE (src)-[r:CALLS {scope: 'external'}]->(tgt)\n"
            "RETURN src, tgt, r"
        ),
        "params": {
            "src_line": src_line,
            "directory": full_directory,
            "file_name": file_name,
            "pkg_dir": pkg,
            "proc_name": proc_name,
        },
    }


def build_calls_internal_query(
    *,
    src_node_type: str, src_line: int,
    proc_name: str,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """같은 파일 내 PROCEDURE/FUNCTION 호출 → CALLS {scope: 'internal'}."""
    return {
        "query": (
            f"MATCH (src:{src_node_type} {{startLine: $src_line, directory: $directory, file_name: $file_name}})\n"
            "OPTIONAL MATCH (tgt {procedure_name: $proc_name, directory: $directory, file_name: $file_name})\n"
            "WHERE tgt:PROCEDURE OR tgt:FUNCTION\n"
            "WITH src, tgt\n"
            "FOREACH (_ IN CASE WHEN tgt IS NOT NULL THEN [1] ELSE [] END |\n"
            "  MERGE (src)-[:CALLS {scope: 'internal'}]->(tgt)\n"
            ")\n"
            "RETURN src"
        ),
        "params": {
            "src_line": src_line,
            "directory": full_directory,
            "file_name": file_name,
            "proc_name": proc_name,
        },
    }


def build_calls_external_placeholder_query(
    *,
    src_node_type: str, src_line: int,
    proc_name: str,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """파일 외 미해상 호출 → EXTERNAL placeholder + CALLS."""
    return {
        "query": (
            f"MATCH (src:{src_node_type} {{startLine: $src_line, directory: $directory, file_name: $file_name}})\n"
            "MERGE (tgt:PROCEDURE:EXTERNAL {procedure_name: $proc_name})\n"
            "MERGE (src)-[r:CALLS {scope: 'external'}]->(tgt)\n"
            "RETURN src, tgt, r"
        ),
        "params": {
            "src_line": src_line,
            "directory": full_directory,
            "file_name": file_name,
            "proc_name": proc_name,
        },
    }


def build_references_query(
    *,
    src_node_type: str, src_line: int,
    var_name: str,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """변수명 토큰 매칭 → (node)-[:REFERENCES]->(:VARIABLE | :PACKAGE_VARIABLE | :CONSTANT_FIELD)."""
    return {
        "query": (
            f"MATCH (n:{src_node_type} {{startLine: $n_line, directory: $directory, file_name: $file_name}})\n"
            "MATCH (v {name: $var_name, directory: $directory, file_name: $file_name})\n"
            "WHERE v:VARIABLE OR v:PACKAGE_VARIABLE OR v:CONSTANT_FIELD\n"
            "MERGE (n)-[:REFERENCES]->(v)\n"
            "RETURN n, v"
        ),
        "params": {
            "n_line": src_line,
            "var_name": var_name,
            "directory": full_directory,
            "file_name": file_name,
        },
    }


def build_init_by_query(
    *,
    src_name: str, src_line: int,
    target_name: str,
    full_directory: str, file_name: str,
) -> Dict[str, Any]:
    """초기화식 식별자 매칭 → (:VARIABLE)-[:INIT_BY]->(:VARIABLE | :PACKAGE_VARIABLE | :CONSTANT_FIELD).

    src 는 init_value 가 단순 식별자인 VARIABLE.
    target 은 같은 파일 내 동일 이름 variable-like 노드.
    """
    return {
        "query": (
            "MATCH (s:VARIABLE {name: $src_name, startLine: $src_line, directory: $directory, file_name: $file_name})\n"
            "MATCH (t {name: $target_name, directory: $directory, file_name: $file_name})\n"
            "WHERE t:VARIABLE OR t:PACKAGE_VARIABLE OR t:CONSTANT_FIELD\n"
            "MERGE (s)-[:INIT_BY]->(t)\n"
            "RETURN s, t"
        ),
        "params": {
            "src_name": src_name,
            "src_line": src_line,
            "target_name": target_name,
            "directory": full_directory,
            "file_name": file_name,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# AST 정적 노드 / 분석 결과 / DML 테이블 / Column / DBLink / FK 빌더
# (이전: dbms/analysis/ast_analyzer.py 안 인라인 Cypher)
# ═══════════════════════════════════════════════════════════════════════════


def build_static_node_query(
    *,
    label: str,
    params: Dict[str, Any],
    set_clause: str,
    is_procedure_with_name: bool,
    has_schema: bool,
) -> Dict[str, Any]:
    """정적 노드 MERGE 쿼리.

    PROCEDURE/FUNCTION/TRIGGER + procedure_name 있는 경우는 composite key (schema 유무 따라 분기).
    그 외에는 startLine + directory + file_name 으로 key.
    """
    if is_procedure_with_name:
        if has_schema:
            query = (
                f"MERGE (__cy_n__:{label} {{schema_name: $schema_name, procedure_name: $procedure_name, "
                f"directory: $directory, file_name: $file_name}})\n"
                f"SET __cy_n__.startLine = $start_line, {set_clause}\n"
                "RETURN __cy_n__"
            )
        else:
            query = (
                f"MERGE (__cy_n__:{label} {{procedure_name: $procedure_name, directory: $directory, "
                f"file_name: $file_name}})\n"
                f"SET __cy_n__.startLine = $start_line, {set_clause}\n"
                "RETURN __cy_n__"
            )
    else:
        query = (
            f"MERGE (__cy_n__:{label} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
            f"SET {set_clause}\n"
            "RETURN __cy_n__"
        )
    return {"query": query, "params": params}


def build_summary_update_query(
    *,
    node_type: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """LLM 분석 결과의 summary/name/code 등을 노드에 SET 하는 쿼리."""
    query = (
        f"MATCH (__cy_n__:{node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
        "SET __cy_n__.endLine = $end_line, __cy_n__.name = $node_name, "
        "__cy_n__.summary = $summary, __cy_n__.node_code = $node_code, "
        "__cy_n__.token = $token, __cy_n__.procedure_name = $procedure_name, "
        "__cy_n__.has_children = $has_children\n"
        "RETURN __cy_n__"
    )
    return {"query": query, "params": params}


def build_actor_role_query(
    *,
    node_type: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """:Actor 노드 + (Actor)-[:ROLE]->(node) 관계 쿼리."""
    query = (
        f"MATCH (__cy_n__:{node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
        "MERGE (a:Actor {name: $actor})\n"
        "ON CREATE SET a.created_at = timestamp()\n"
        "MERGE (a)-[r:ROLE]->(__cy_n__)\n"
        "ON CREATE SET r.created_at = timestamp()\n"
        "RETURN a"
    )
    return {"query": query, "params": params}


def build_create_temp_table_query(
    *,
    node_type: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """CREATE_TEMP_TABLE 노드에 :Table 라벨 추가 + name/schema/db SET."""
    query = (
        f"MATCH (__cy_n__:{node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
        "SET __cy_n__:Table, __cy_n__.name = $name, __cy_n__.schema = $schema, __cy_n__.db = $db\n"
        "RETURN __cy_n__"
    )
    return {"query": query, "params": params}


def build_node_to_table_rel_merge_str(rel_types: List[str]) -> str:
    """노드→Table 관계 MERGE 절 문자열 누적 ('MERGE (__cy_n__)-[:FROM]->(__cy_t__)\\n...').

    rel_types: ``['FROM', 'WRITES']`` 등.
    빈 리스트면 빈 문자열 반환.
    """
    parts = [f"MERGE (__cy_n__)-[__cy_r{i}__:{rel_type}]->(__cy_t__)" for i, rel_type in enumerate(rel_types)]
    return "\n".join(parts)


def build_dml_table_relation_query(
    *,
    node_type: str,
    table_merge_query: str,
    rel_merge_str: str,
    db_link_set: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """DML 노드 → :Table 관계 쿼리.

    Args:
        table_merge_query: build_table_merge 결과의 query (Table MERGE 절)
        rel_merge_str: ``MERGE (__cy_n__)-[:FROM/WRITES]->(__cy_t__)`` 누적 (빈 문자열 가능)
        db_link_set: ``SET __cy_t__.db_link = COALESCE(...)`` 절 (db_link 없으면 빈 문자열)
    """
    base = (
        f"MATCH (__cy_n__:{node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
        "WITH __cy_n__\n"
        f"{table_merge_query}\n"
        "SET __cy_t__.db = coalesce(__cy_t__.db, $db)"
        f"{db_link_set}"
    )
    if rel_merge_str:
        query = base + f"\n{rel_merge_str}\nRETURN __cy_n__, __cy_t__"
    else:
        query = base + "\nRETURN __cy_n__, __cy_t__"
    return {"query": query, "params": params}


def build_column_query(
    *,
    table_merge_query: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """:Column 노드 MERGE + (Table)-[:HAS_COLUMN]->(Column) 관계.

    DDL 보존 가드: description / description_source 가 비어있을 때만 LLM 결과로 채움.
    name / dtype / nullable 도 비어있을 때만 SET (LLM dtype 환각으로 DDL 정확도 손실 방지).
    Framework 의 build_column_queries (graph_queries.py:355-360) 가드 패턴과 정합.
    """
    query = (
        f"{table_merge_query}\nWITH __cy_t__\n"
        "MERGE (__cy_c__:Column {fqn: $fqn})\n"
        "SET __cy_c__.description = CASE\n"
        "      WHEN __cy_c__.description IS NULL OR trim(toString(__cy_c__.description)) = ''\n"
        "      THEN $col_desc ELSE __cy_c__.description END,\n"
        "    __cy_c__.description_source = CASE\n"
        "      WHEN __cy_c__.description IS NULL OR trim(toString(__cy_c__.description)) = ''\n"
        "      THEN 'code' ELSE __cy_c__.description_source END,\n"
        "    __cy_c__.name = COALESCE(__cy_c__.name, $col_name),\n"
        "    __cy_c__.dtype = COALESCE(__cy_c__.dtype, $col_type),\n"
        "    __cy_c__.nullable = COALESCE(__cy_c__.nullable, $nullable)\n"
        "MERGE (__cy_t__)-[__cy_r__:HAS_COLUMN]->(__cy_c__)\n"
        "RETURN __cy_t__, __cy_c__, __cy_r__"
    )
    return {"query": query, "params": params}


def build_dblink_query(
    *,
    node_type: str,
    remote_table_merge_query: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """:DBLink 노드 + :Table → DBLink LINKS_TO + node → Table DB_LINK 관계."""
    query = (
        f"{remote_table_merge_query}\n"
        "SET __cy_t__.db_link = $link_name\n"
        "WITH __cy_t__\n"
        "MERGE (__cy_l__:DBLink {name: $link_name})\n"
        "MERGE (__cy_l__)-[__cy_r1__:LINKS_TO]->(__cy_t__)\n"
        "WITH __cy_t__, __cy_l__\n"
        f"MATCH (__cy_n__:{node_type} {{startLine: $start_line, directory: $directory, file_name: $file_name}})\n"
        "MERGE (__cy_n__)-[__cy_r2__:DB_LINK {mode: $mode}]->(__cy_t__)\n"
        "RETURN __cy_l__, __cy_t__, __cy_n__"
    )
    return {"query": query, "params": params}


def build_fk_to_table_query(
    *,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """(Table)-[:FK_TO_TABLE]->(Table) 관계 쿼리."""
    query = (
        "MATCH (__cy_st__:Table {schema: $src_schema, name: $src_name, db: $db})\n"
        "MATCH (__cy_tt__:Table {schema: $tgt_schema, name: $tgt_name, db: $db})\n"
        "MERGE (__cy_st__)-[__cy_r__:FK_TO_TABLE {sourceColumn: $src_col, targetColumn: $tgt_col}]->(__cy_tt__)\n"
        "ON CREATE SET __cy_r__.type = 'many_to_one', __cy_r__.source = 'procedure'\n"
        "RETURN __cy_st__, __cy_tt__, __cy_r__"
    )
    return {"query": query, "params": params}


def build_fk_to_column_query(
    *,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """(Column)-[:FK_TO_COLUMN]->(Column) 관계 쿼리."""
    query = (
        "MATCH (__cy_sc__:Column {fqn: $src_fqn})\n"
        "MATCH (__cy_dc__:Column {fqn: $tgt_fqn})\n"
        "MERGE (__cy_sc__)-[__cy_r__:FK_TO_COLUMN]->(__cy_dc__)\n"
        "ON CREATE SET __cy_r__.source = 'procedure'\n"
        "RETURN __cy_sc__, __cy_dc__, __cy_r__"
    )
    return {"query": query, "params": params}


__all__ = [
    "build_procedure_summary_queries",
    "build_table_description_query",
    "build_column_description_query",
    "build_package_queries",
    "build_has_procedure_query",
    "build_parent_of_query",
    "build_next_query",
    "build_table_merge",
    "build_calls_external_pkg_query",
    "build_calls_internal_query",
    "build_calls_external_placeholder_query",
    "build_references_query",
    "build_static_node_query",
    "build_summary_update_query",
    "build_actor_role_query",
    "build_create_temp_table_query",
    "build_node_to_table_rel_merge_str",
    "build_dml_table_relation_query",
    "build_column_query",
    "build_dblink_query",
    "build_fk_to_table_query",
    "build_fk_to_column_query",
    "TABLE_DESCRIPTION_UPDATE_QUERY",
    "COLUMN_DESCRIPTION_UPDATE_QUERY",
]
