"""DBMS strategy 의 Cypher 빌더 모음.

Framework 의 `framework/common/graph_queries.py` 와 동일한 책임 — DBMS 측 Neo4j
쿼리 빌더를 한 곳에 모은다. 이전에는 `phase_summary.py` (647줄) 와
`analysis/ast_analyzer.py` 안에 산재해 있었다.

EM (Rule/Example/Question) 빌더는 양쪽 strategy 가 공유하는
`analyzer/common/cypher/example_mapping.py` 의 `build_em_queries` 를
DBMS anchor 형태로 wrapping 해서 호출.
"""

from __future__ import annotations

from typing import Any, Dict, List

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
    actor: str = "",
) -> List[Dict[str, Any]]:
    """프로시저 1개 단위의 Summary + Example Mapping + Actor ROLE Cypher 쿼리 리스트.

    흐름:
      1. PROCEDURE.summary SET
      2. EM (Rule/Example/Question) Cypher — `analyzer/common/cypher/example_mapping`
         의 공용 빌더 호출
      3. Actor 노드 + (Actor)-[:ROLE]->(PROCEDURE) — actor 인자 있을 때만

    Args:
        procedure_type: PROCEDURE | FUNCTION | TRIGGER
        procedure_name, schema_name, directory, file_name: anchor composite key
        summary: 프로시저 요약 텍스트
        analysis: ``{"rules": [...], "examples": [...], "questions": [...]}``
        actor: 자식 노드들의 LLM actor 응답을 phase_summary 가 집계해 넘김. 빈 문자열이면 ROLE 생성 X.
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

    # 3) Actor 노드 + ROLE 관계 — unit-root 에 부착
    if actor:
        queries.append({
            "query": (
                "UNWIND $rows AS row\n"
                f"{anchor_match}\n"
                "MERGE (act:Actor {name: row.actor})\n"
                "ON CREATE SET act.created_at = timestamp()\n"
                "MERGE (act)-[r:ROLE]->(a)\n"
                "ON CREATE SET r.created_at = timestamp()\n"
                "RETURN act, r"
            ),
            "params": {"rows": [{**anchor_keys, "actor": str(actor)}]},
        })

    return queries


# ═══════════════════════════════════════════════════════════════════════════
# Table / Column description 보강 쿼리 — phase_summary 의 LLM 결과 적용용
# ═══════════════════════════════════════════════════════════════════════════


_TABLE_DESCRIPTION_UNWIND = (
    "UNWIND $items AS row\n"
    "MATCH (__cy_t__:Table {schema: row.schema, name: row.name, db: row.db})\n"
    "SET __cy_t__.analyzed_description = row.llm_desc\n"
    "WITH __cy_t__, row\n"
    "WHERE __cy_t__.description IS NULL OR __cy_t__.description = ''\n"
    "SET __cy_t__.description = row.llm_desc, __cy_t__.description_source = 'code'"
)


_COLUMN_DESCRIPTION_UNWIND = (
    "UNWIND $items AS row\n"
    "MATCH (__cy_c__:Column {fqn: row.fqn})\n"
    "SET __cy_c__.analyzed_description = row.llm_desc\n"
    "WITH __cy_c__, row\n"
    "WHERE __cy_c__.description IS NULL OR __cy_c__.description = ''\n"
    "SET __cy_c__.description = row.llm_desc, __cy_c__.description_source = 'code'"
)


def build_table_description_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """:Table 노드의 설명 보강 (UNWIND 단일 쿼리).

    items: 각 dict 에 ``{"schema", "name", "db", "llm_desc"}``.
    """
    if not items:
        return []
    return [{"query": _TABLE_DESCRIPTION_UNWIND, "params": {"items": items}}]


def build_column_description_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """:Column 노드의 설명 보강 (UNWIND 단일 쿼리).

    items: 각 dict 에 ``{"datasource", "schema", "table_name", "column_name", "llm_desc"}``.
    fqn 은 lower case 의 ``datasource.schema.table.column`` 4-part 형식
    (DDL handler 와 동일 규칙 — datasource 빠지면 MATCH 실패).
    """
    if not items:
        return []
    rows = [
        {
            "fqn": ".".join(filter(None, [
                it.get("datasource", ""), it.get("schema", ""),
                it.get("table_name", ""), it.get("column_name", ""),
            ])).lower(),
            "llm_desc": it.get("llm_desc", ""),
        }
        for it in items
    ]
    return [{"query": _COLUMN_DESCRIPTION_UNWIND, "params": {"items": rows}}]


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


def _build_label_pair_rel_unwind(rel_type: str, src_var: str, dst_var: str, src_name_field: str, dst_name_field: str) -> str:
    """관계 MERGE UNWIND 쿼리 생성 (label 동적 — `(parent_label, child_label)` 별 그룹).

    label 은 Cypher 변수로 못 쓰므로 호출자가 그룹 단위로 같은 label pair 의 items 를 모음.
    src_name_field, dst_name_field: row 안의 src/dst 이름 필드명 (예: parent_name, child_name).

    src_var/dst_var 는 ``"a:LABEL"`` / ``"b:LABEL"`` 형태 (MATCH 패턴용).
    MERGE 절은 이미 바인딩된 변수만 — Neo4j 5+ 가 바인딩된 변수에 레이블 재지정 금지하므로
    ``a``, ``b`` 만 사용.
    """
    return (
        f"UNWIND $rows AS row\n"
        f"MATCH ({src_var} {{name: row.{src_name_field}, directory: row.directory, file_name: row.file_name}})\n"
        f"MATCH ({dst_var} {{name: row.{dst_name_field}, directory: row.directory, file_name: row.file_name}})\n"
        f"MERGE (a)-[:{rel_type}]->(b)"
    )


def _emit_label_pair_rel_queries(
    items: List[Dict[str, Any]],
    *,
    src_label_field: str, dst_label_field: str,
    src_name_field: str, dst_name_field: str,
    rel_type: str,
) -> List[Dict[str, Any]]:
    """(src_label, dst_label) 별로 UNWIND 그룹화. 라벨 쌍별 1쿼리.

    items: 각 dict 에 src/dst label/name + directory + file_name.
    """
    if not items:
        return []
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in items:
        key = (it[src_label_field], it[dst_label_field])
        groups.setdefault(key, []).append({
            src_name_field: it[src_name_field],
            dst_name_field: it[dst_name_field],
            "directory": it["directory"],
            "file_name": it["file_name"],
        })
    queries: List[Dict[str, Any]] = []
    for (src_label, dst_label), rows in groups.items():
        src_var = f"a:{src_label}"
        dst_var = f"b:{dst_label}"
        query = _build_label_pair_rel_unwind(rel_type, src_var, dst_var, src_name_field, dst_name_field)
        queries.append({"query": query, "params": {"rows": rows}})
    return queries


def build_has_procedure_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(FILE)-[:HAS_PROCEDURE]->(PROCEDURE/FUNCTION) 관계 — UNWIND, label 쌍별 그룹.

    items: 각 dict 에 ``{parent_type, parent_name, child_type, child_name, directory, file_name}``.
    """
    return _emit_label_pair_rel_queries(
        items,
        src_label_field="parent_type", dst_label_field="child_type",
        src_name_field="parent_name", dst_name_field="child_name",
        rel_type="HAS_PROCEDURE",
    )


def build_parent_of_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(parent)-[:PARENT_OF]->(child) 관계 — UNWIND, label 쌍별 그룹."""
    return _emit_label_pair_rel_queries(
        items,
        src_label_field="parent_type", dst_label_field="child_type",
        src_name_field="parent_name", dst_name_field="child_name",
        rel_type="PARENT_OF",
    )


def build_next_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(prev)-[:NEXT]->(curr) 관계 — UNWIND, label 쌍별 그룹.

    items: 각 dict 에 ``{prev_type, prev_name, curr_type, curr_name, directory, file_name}``.
    """
    return _emit_label_pair_rel_queries(
        items,
        src_label_field="prev_type", dst_label_field="curr_type",
        src_name_field="prev_name", dst_name_field="curr_name",
        rel_type="NEXT",
    )


# ═══════════════════════════════════════════════════════════════════════════
# CALLS / REFERENCES — 결정론 링킹 (call_linker / variable_linker 위임용)
# ═══════════════════════════════════════════════════════════════════════════


def _calls_unwind(src_label: str, tgt_clause: str, scope: str, optional_match: bool = False) -> str:
    """CALLS 관계 UNWIND 쿼리 생성. tgt_clause = MERGE/MATCH 절 (다양한 패턴)."""
    base = (
        "UNWIND $rows AS row\n"
        f"MATCH (src:{src_label} {{startLine: row.src_line, directory: row.directory, file_name: row.file_name}})\n"
    )
    if optional_match:
        return (
            base
            + tgt_clause
            + "\nWITH src, tgt\n"
            "FOREACH (_ IN CASE WHEN tgt IS NOT NULL THEN [1] ELSE [] END |\n"
            f"  MERGE (src)-[:CALLS {{scope: '{scope}'}}]->(tgt)\n"
            ")"
        )
    return (
        base
        + tgt_clause
        + f"\nMERGE (src)-[r:CALLS {{scope: '{scope}'}}]->(tgt)"
    )


def build_calls_external_pkg_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``PKG.FUNC()`` 외부 패키지 호출 → CALLS {scope: 'external'}. UNWIND, src label 별 그룹.

    items: ``{src_node_type, src_line, pkg, proc_name, directory, file_name}``.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["src_node_type"], []).append({
            "src_line":  it["src_line"],
            "directory": it["directory"],
            "file_name": it["file_name"],
            "pkg_dir":   it["pkg"],
            "proc_name": it["proc_name"],
        })
    queries: List[Dict[str, Any]] = []
    for src_label, rows in groups.items():
        tgt = "MERGE (tgt:PROCEDURE {directory: row.pkg_dir, procedure_name: row.proc_name})"
        queries.append({
            "query": _calls_unwind(src_label, tgt, "external", optional_match=False),
            "params": {"rows": rows},
        })
    return queries


def build_calls_internal_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """같은 파일 내 PROCEDURE/FUNCTION 호출 → CALLS {scope: 'internal'}. UNWIND, src label 별 그룹.

    items: ``{src_node_type, src_line, proc_name, directory, file_name}``.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["src_node_type"], []).append({
            "src_line":  it["src_line"],
            "directory": it["directory"],
            "file_name": it["file_name"],
            "proc_name": it["proc_name"],
        })
    queries: List[Dict[str, Any]] = []
    for src_label, rows in groups.items():
        tgt = (
            "OPTIONAL MATCH (tgt {procedure_name: row.proc_name, directory: row.directory, file_name: row.file_name})\n"
            "WHERE tgt:PROCEDURE OR tgt:FUNCTION"
        )
        queries.append({
            "query": _calls_unwind(src_label, tgt, "internal", optional_match=True),
            "params": {"rows": rows},
        })
    return queries


def build_calls_external_placeholder_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """파일 외 미해상 호출 → EXTERNAL placeholder + CALLS. UNWIND, src label 별 그룹.

    items: ``{src_node_type, src_line, proc_name, directory, file_name}``.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["src_node_type"], []).append({
            "src_line":  it["src_line"],
            "directory": it["directory"],
            "file_name": it["file_name"],
            "proc_name": it["proc_name"],
        })
    queries: List[Dict[str, Any]] = []
    for src_label, rows in groups.items():
        tgt = "MERGE (tgt:PROCEDURE:EXTERNAL {procedure_name: row.proc_name})"
        queries.append({
            "query": _calls_unwind(src_label, tgt, "external", optional_match=False),
            "params": {"rows": rows},
        })
    return queries


def build_references_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """변수명 토큰 매칭 → (node)-[:REFERENCES]->(VARIABLE-like). UNWIND, src label 별 그룹.

    items: 각 dict 에 ``{src_node_type, src_line, var_name, directory, file_name}``.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["src_node_type"], []).append({
            "n_line":    it["src_line"],
            "var_name":  it["var_name"],
            "directory": it["directory"],
            "file_name": it["file_name"],
        })
    queries: List[Dict[str, Any]] = []
    for src_label, rows in groups.items():
        query = (
            "UNWIND $rows AS row\n"
            f"MATCH (n:{src_label} {{startLine: row.n_line, directory: row.directory, file_name: row.file_name}})\n"
            "MATCH (v {name: row.var_name, directory: row.directory, file_name: row.file_name})\n"
            "WHERE v:VARIABLE OR v:PACKAGE_VARIABLE OR v:CONSTANT_FIELD OR v:PARAMETER\n"
            "MERGE (n)-[:REFERENCES]->(v)"
        )
        queries.append({"query": query, "params": {"rows": rows}})
    return queries


def build_init_by_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """초기화식 식별자 매칭 → (:VARIABLE)-[:INIT_BY]->(:VARIABLE-like). UNWIND 단일 쿼리.

    src 는 항상 VARIABLE. target 은 같은 파일 내 동일 이름 variable-like 노드.
    items: 각 dict 에 ``{src_name, src_line, target_name, directory, file_name}``.
    """
    if not items:
        return []
    rows = [
        {
            "src_name":    it["src_name"],
            "src_line":    it["src_line"],
            "target_name": it["target_name"],
            "directory":   it["directory"],
            "file_name":   it["file_name"],
        }
        for it in items
    ]
    query = (
        "UNWIND $rows AS row\n"
        "MATCH (s:VARIABLE {name: row.src_name, startLine: row.src_line, directory: row.directory, file_name: row.file_name})\n"
        "MATCH (t {name: row.target_name, directory: row.directory, file_name: row.file_name})\n"
        "WHERE t:VARIABLE OR t:PACKAGE_VARIABLE OR t:CONSTANT_FIELD OR t:PARAMETER\n"
        "MERGE (s)-[:INIT_BY]->(t)"
    )
    return [{"query": query, "params": {"rows": rows}}]


# ═══════════════════════════════════════════════════════════════════════════
# AST 정적 노드 / 분석 결과 / DML 테이블 / Column / DBLink / FK 빌더
# (이전: dbms/analysis/ast_analyzer.py 안 인라인 Cypher)
# ═══════════════════════════════════════════════════════════════════════════


def build_static_node_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """정적 노드 MERGE — UNWIND, label 별 그룹화.

    items: 각 dict 에 ``{"label", "node_name", "directory", "file_name", "start_line", "props"}``.
    ``props`` 는 추가 속성 dict (각 노드별 다른 속성 — n += row.props 로 SET).

    MERGE 키 = (label, name, directory, file_name). 같은 파일 안에서 (label, name) 충돌 0.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["label"], []).append({
            "node_name":  it["node_name"],
            "directory":  it["directory"],
            "file_name":  it["file_name"],
            "start_line": it["start_line"],
            "props":      it.get("props") or {},
        })
    queries: List[Dict[str, Any]] = []
    for label, rows in groups.items():
        query = (
            "UNWIND $rows AS row\n"
            f"MERGE (n:{label} {{name: row.node_name, directory: row.directory, file_name: row.file_name}})\n"
            "SET n.startLine = row.start_line, n += row.props"
        )
        queries.append({"query": query, "params": {"rows": rows}})
    return queries


def build_summary_update_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """LLM 분석 결과의 summary/name/code 등 SET — UNWIND, node_type 별 그룹.

    items: 각 dict 에 ``{"node_type", "start_line", "directory", "file_name", + 갱신할 속성들}``.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        node_type = it["node_type"]
        row = {k: v for k, v in it.items() if k != "node_type"}
        groups.setdefault(node_type, []).append(row)
    queries: List[Dict[str, Any]] = []
    for node_type, rows in groups.items():
        query = (
            "UNWIND $rows AS row\n"
            f"MATCH (n:{node_type} {{startLine: row.start_line, directory: row.directory, file_name: row.file_name}})\n"
            "SET n.endLine = row.end_line, n.name = row.node_name, n.summary = row.summary, "
            "n.node_code = row.node_code, n.token = row.token, n.procedure_name = row.procedure_name, "
            "n.has_children = row.has_children"
        )
        queries.append({"query": query, "params": {"rows": rows}})
    return queries


def build_create_temp_table_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """CREATE_TEMP_TABLE 노드에 :Table 라벨 추가 + name/schema/db SET — UNWIND, node_type 별.

    items: 각 dict 에 ``{"node_type", "start_line", "directory", "file_name", "name", "schema", "db"}``.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["node_type"], []).append({
            "start_line": it["start_line"],
            "directory":  it["directory"],
            "file_name":  it["file_name"],
            "name":       it["name"],
            "schema":     it["schema"],
            "db":         it["db"],
        })
    queries: List[Dict[str, Any]] = []
    for node_type, rows in groups.items():
        query = (
            "UNWIND $rows AS row\n"
            f"MATCH (n:{node_type} {{startLine: row.start_line, directory: row.directory, file_name: row.file_name}})\n"
            "SET n:Table, n.name = row.name, n.schema = row.schema, n.db = row.db"
        )
        queries.append({"query": query, "params": {"rows": rows}})
    return queries


def build_dml_table_relation_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DML 노드 → :Table 관계 — UNWIND, (node_type, rel_types, has_db_link) 그룹."""
    if not items:
        return []
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in items:
        rel_key = tuple(it.get("rel_types") or [])
        has_db_link = it.get("db_link") is not None
        groups.setdefault((it["node_type"], rel_key, has_db_link), []).append({
            "start_line":  it["start_line"],
            "directory":   it["directory"],
            "file_name":   it["file_name"],
            "db":          it["db"],
            "schema_name": it["schema_name"],
            "table_name":  it["table_name"],
            "op":          (it.get("op") or "").strip().upper(),
            "db_link":     it.get("db_link"),
        })
    queries: List[Dict[str, Any]] = []
    for (node_type, rel_types, has_db_link), rows in groups.items():
        rel_merges = "\n".join(
            f"MERGE (n)-[:{rt} {{op: row.op}}]->(t)" for rt in rel_types
        )
        db_link_set = "\nSET t.db_link = COALESCE(t.db_link, row.db_link)" if has_db_link else ""
        query = (
            "UNWIND $rows AS row\n"
            f"MATCH (n:{node_type} {{startLine: row.start_line, directory: row.directory, file_name: row.file_name}})\n"
            "MERGE (s:Schema {db: row.db, name: row.schema_name})\n"
            "MERGE (t:Table {name: row.table_name, schema: row.schema_name, db: row.db})\n"
            "MERGE (t)-[:BELONGS_TO]->(s)"
            f"{db_link_set}"
        )
        if rel_merges:
            query += "\n" + rel_merges
        queries.append({"query": query, "params": {"rows": rows}})
    return queries


def build_column_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """:Column 노드 MERGE + (Table)-[:HAS_COLUMN]->(Column) — UNWIND 단일 쿼리.

    items: 각 dict 에 ``{db, schema_name, table_name, fqn, col_name, col_type,
    col_desc, nullable}``.

    DDL 보존 가드: description / description_source / name / dtype / nullable 모두
    비어있을 때만 LLM 결과로 채움 (LLM dtype 환각으로 DDL 정확도 손실 방지).
    """
    if not items:
        return []
    query = (
        "UNWIND $rows AS row\n"
        "MERGE (s:Schema {db: row.db, name: row.schema_name})\n"
        "MERGE (t:Table {name: row.table_name, schema: row.schema_name, db: row.db})\n"
        "MERGE (t)-[:BELONGS_TO]->(s)\n"
        "WITH t, row\n"
        "MERGE (c:Column {fqn: row.fqn})\n"
        "SET c.description = CASE\n"
        "      WHEN c.description IS NULL OR trim(toString(c.description)) = ''\n"
        "      THEN row.col_desc ELSE c.description END,\n"
        "    c.description_source = CASE\n"
        "      WHEN c.description IS NULL OR trim(toString(c.description)) = ''\n"
        "      THEN 'code' ELSE c.description_source END,\n"
        "    c.name = COALESCE(c.name, row.col_name),\n"
        "    c.dtype = COALESCE(c.dtype, row.col_type),\n"
        "    c.nullable = COALESCE(c.nullable, row.nullable)\n"
        "MERGE (t)-[:HAS_COLUMN]->(c)"
    )
    return [{"query": query, "params": {"rows": items}}]


def build_dblink_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """:DBLink 노드 + :Table → DBLink LINKS_TO + node → Table DB_LINK — UNWIND, node_type 그룹.

    items: 각 dict 에 ``{node_type, start_line, directory, file_name,
    db, schema_name, table_name, link_name, mode}``.
    """
    if not items:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["node_type"], []).append({
            "start_line":  it["start_line"],
            "directory":   it["directory"],
            "file_name":   it["file_name"],
            "db":          it["db"],
            "schema_name": it["schema_name"],
            "table_name":  it["table_name"],
            "link_name":   it["link_name"],
            "mode":        it["mode"],
        })
    queries: List[Dict[str, Any]] = []
    for node_type, rows in groups.items():
        query = (
            "UNWIND $rows AS row\n"
            f"MATCH (n:{node_type} {{startLine: row.start_line, directory: row.directory, file_name: row.file_name}})\n"
            "MERGE (s:Schema {db: row.db, name: row.schema_name})\n"
            "MERGE (t:Table {name: row.table_name, schema: row.schema_name, db: row.db})\n"
            "MERGE (t)-[:BELONGS_TO]->(s)\n"
            "SET t.db_link = row.link_name\n"
            "WITH n, t, row\n"
            "MERGE (l:DBLink {name: row.link_name})\n"
            "MERGE (l)-[:LINKS_TO]->(t)\n"
            "MERGE (n)-[:DB_LINK {mode: row.mode}]->(t)"
        )
        queries.append({"query": query, "params": {"rows": rows}})
    return queries


def build_fk_to_table_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(Table)-[:FK_TO_TABLE]->(Table) 관계 — UNWIND 단일 쿼리.

    items: ``{db, src_schema, src_name, tgt_schema, tgt_name, src_col, tgt_col}``.
    """
    if not items:
        return []
    query = (
        "UNWIND $rows AS row\n"
        "MATCH (st:Table {schema: row.src_schema, name: row.src_name, db: row.db})\n"
        "MATCH (tt:Table {schema: row.tgt_schema, name: row.tgt_name, db: row.db})\n"
        "MERGE (st)-[r:FK_TO_TABLE {sourceColumn: row.src_col, targetColumn: row.tgt_col}]->(tt)\n"
        "ON CREATE SET r.type = 'many_to_one', r.source = 'procedure'"
    )
    return [{"query": query, "params": {"rows": items}}]


def build_fk_to_column_queries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """(Column)-[:FK_TO_COLUMN]->(Column) 관계 — UNWIND 단일 쿼리.

    items: ``{src_fqn, tgt_fqn}``.
    """
    if not items:
        return []
    query = (
        "UNWIND $rows AS row\n"
        "MATCH (sc:Column {fqn: row.src_fqn})\n"
        "MATCH (dc:Column {fqn: row.tgt_fqn})\n"
        "MERGE (sc)-[r:FK_TO_COLUMN]->(dc)\n"
        "ON CREATE SET r.source = 'procedure'"
    )
    return [{"query": query, "params": {"rows": items}}]


__all__ = [
    "build_procedure_summary_queries",
    "build_table_description_queries",
    "build_column_description_queries",
    "build_package_queries",
    "build_has_procedure_queries",
    "build_parent_of_queries",
    "build_next_queries",
    "build_calls_external_pkg_queries",
    "build_calls_internal_queries",
    "build_calls_external_placeholder_queries",
    "build_references_queries",
    "build_init_by_queries",
    "build_static_node_queries",
    "build_summary_update_queries",
    "build_create_temp_table_queries",
    "build_dml_table_relation_queries",
    "build_column_queries",
    "build_dblink_queries",
    "build_fk_to_table_queries",
    "build_fk_to_column_queries",
]
