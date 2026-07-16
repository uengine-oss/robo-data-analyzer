"""DDL 파싱 — sqlglot 다중 방언 + Oracle 정규화 + COMMENT ON.

CREATE TABLE / VIEW / ALTER TABLE ... FK 등을 파싱해
``(Schema, Table, Column, Fk, HasColumn, BelongsTo)`` 튜플 반환.

DDL = 구조의 진실 기준 (plan §3.1) — 컬럼 타입·제약·기본값·FK·설명을 결정론 추출:
- description = ``COMMENT ON`` (정규식) 우선, 없으면 ``--`` / ``/* */`` 주석 —
  둘 다 ``description_source`` = "ddl" (DDL 파일 출처).
- ``--`` / ``/* */`` 원문은 Table/Column.comment 에 항상 보존 (분석 입력 단서).
- ``CREATE SCHEMA`` / ``SET search_path`` → 스키마 인식 (비수식 테이블에 스키마·fqn 부여).
- Oracle 물리옵션(TABLESPACE/STORAGE/…)·``VARCHAR2(n BYTE)``·``/`` 종결자 → 정규화 후 파싱.
- 제약(PK/UNIQUE/NOT NULL) — 컬럼 레벨 + 테이블 레벨 ``PRIMARY KEY`` 둘 다.
- DEFAULT → ``Column.default_value``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import sqlglot.expressions as exp

from graph.schema.graph_edges import BelongsTo, Fk, HasColumn
from graph.schema.graph_nodes import Column, Schema, Table
from graph.schema.node_ids import db_id, fqn


@dataclass
class DDLParseResult:
    """단일 DDL 파일 파싱 결과."""
    schemas: list[Schema] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    fk_edges: list[Fk] = field(default_factory=list)
    has_column_edges: list[HasColumn] = field(default_factory=list)
    belongs_to_edges: list[BelongsTo] = field(default_factory=list)


# ── DDL 정규화 / dialect 감지 (정규식) ─────────────────────

# dialect 마커 — sqlglot 자동감지 의존 탈피 (plan M6).
_DIALECT_MARKERS: list[tuple[str, re.Pattern]] = [
    ("oracle",   re.compile(r"\bN?VARCHAR2\b|\bNUMBER\s*\(|\bTABLESPACE\b|\bNOLOGGING\b", re.I)),
    ("postgres", re.compile(r"\bSERIAL\b|\bBIGSERIAL\b|\bBYTEA\b", re.I)),
    ("mysql",    re.compile(r"\bENGINE\s*=|\bAUTO_INCREMENT\b", re.I)),
]

# 파싱 전 제거 — COMMENT ON 은 정규식으로 별도 처리, 물리옵션은 분석 무가치.
_COMMENT_ON_STMT = re.compile(
    r"COMMENT\s+ON\s+(?:TABLE|VIEW|COLUMN)\s+[\w.\"]+\s+IS\s+'(?:[^']|'')*'\s*;?", re.I,
)
_VARCHAR_LEN_UNIT = re.compile(r"(N?VARCHAR2?\s*\(\s*\d+)\s+(?:BYTE|CHAR)\s*\)", re.I)
_STORAGE_OPTS = re.compile(
    r"\bTABLESPACE\s+\"?\w+\"?"
    r"|\bSTORAGE\s*\([^)]*\)"
    r"|\bPCT(?:FREE|USED)\s+\d+"
    r"|\b(?:INI|MAX)TRANS\s+\d+"
    r"|\b(?:NO)?LOGGING\b",
    re.I,
)
_SLASH_TERMINATOR = re.compile(r"^\s*/\s*$", re.M)

# COMMENT ON 추출 — VIEW 도 테이블과 동일 취급 (description 의 출처).
_COMMENT_TABLE = re.compile(
    r"COMMENT\s+ON\s+(?:TABLE|VIEW)\s+([\w.\"]+)\s+IS\s+'((?:[^']|'')*)'", re.I,
)
_COMMENT_COLUMN = re.compile(
    r"COMMENT\s+ON\s+COLUMN\s+([\w.\"]+)\s+IS\s+'((?:[^']|'')*)'", re.I,
)


def _detect_dialect(sql: str) -> str | None:
    """DDL 본문 마커로 방언 감지 — 없으면 None (sqlglot 기본)."""
    for name, pattern in _DIALECT_MARKERS:
        if pattern.search(sql):
            return name
    return None


def _normalize_ddl(sql: str, dialect: str | None) -> tuple[str, str | None]:
    """파싱 전처리 — COMMENT ON·물리옵션·BYTE 단위·``/`` 종결자 제거 + dialect 결정."""
    effective = dialect or _detect_dialect(sql)
    sql = _COMMENT_ON_STMT.sub("", sql)          # COMMENT ON 은 regex 로 별도 처리
    sql = _VARCHAR_LEN_UNIT.sub(r"\1)", sql)     # VARCHAR2(n BYTE) → VARCHAR2(n)
    sql = _STORAGE_OPTS.sub("", sql)             # 물리 저장옵션 제거 (분석 무가치)
    sql = _SLASH_TERMINATOR.sub("", sql)         # SQL*Plus / 종결자 제거
    return sql, effective


def _ref_segments(ref: str) -> list[str]:
    """``"SCHEMA".TABLE`` → ``["schema", "table"]`` (따옴표 제거·소문자)."""
    return [seg.strip('"').lower() for seg in ref.split(".") if seg.strip('"')]


def _extract_comment_on(sql: str) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """``COMMENT ON`` → (table_comments, column_comments).

    키는 소문자 — table 이름 / (table, column). escape 된 ``''`` 는 ``'`` 로 복원.
    """
    table_comments: dict[str, str] = {}
    column_comments: dict[tuple[str, str], str] = {}
    for m in _COMMENT_TABLE.finditer(sql):
        ref = _ref_segments(m.group(1))
        if ref:
            table_comments[ref[-1]] = m.group(2).replace("''", "'")
    for m in _COMMENT_COLUMN.finditer(sql):
        ref = _ref_segments(m.group(1))
        if len(ref) >= 2:
            column_comments[(ref[-2], ref[-1])] = m.group(2).replace("''", "'")
    return table_comments, column_comments


# ── search_path / 일반주석 ────────────────────────────────────

_SEARCH_PATH_RE = re.compile(r"search_path\s*(?:=|TO)\s*([^\s,;]+)", re.I)


def _extract_search_path(stmt: exp.Expression) -> str:
    """``SET search_path TO x`` → 스키마 이름. 매칭 없으면 빈 문자열.

    sqlglot 의 SET 표현은 dialect 별로 갈려 — 구조 의존 대신 ``.sql()`` 텍스트를 regex.
    """
    m = _SEARCH_PATH_RE.search(stmt.sql())
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def _join_comments(comments: list[str] | None) -> str:
    """sqlglot 가 노드에 부착한 ``--`` / ``/* */`` 주석 → 한 줄 (공백 결합).

    구분선(``-----`` ``=====`` 등 영숫자 없는 줄)은 노이즈라 제외.
    """
    if not comments:
        return ""
    lines: list[str] = []
    for c in comments:
        s = (c or "").strip()
        if s and any(ch.isalnum() for ch in s):
            lines.append(s)
    return " ".join(lines)


def _resolve_description(formal: str, raw_comment: str) -> tuple[str, str]:
    """description + 출처 결정 — ``COMMENT ON`` 우선, 없으면 ``--`` 주석.

    Returns ``(description, description_source)``:
    - ``COMMENT ON`` 또는 ``--`` 주석에서 왔으면 → ``(..., "ddl")``
    - 둘 다 없으면 → ``("", "")``

    COMMENT ON 이든 ``--`` 든 DDL 파일에서 온 설명이라 출처는 동일하게 "ddl".
    """
    description = formal or raw_comment
    return description, ("ddl" if description else "")


# ── 파일 진입점 ───────────────────────────────────────────────

def parse_ddl_file(
    path: Path,
    *,
    db: str,
    default_schema: str = "",
    dialect: str | None = None,
) -> DDLParseResult:
    """DDL 파일 1개 파싱 → 도메인 노드 / 엣지.

    Args:
        path: DDL 파일 경로.
        db: datasource 이름 (Neo4j 의 db 속성).
        default_schema: 파일 안 명시 안 된 경우의 기본 스키마.
        dialect: sqlglot dialect. ``None`` (기본) = 본문 마커로 자동 감지
                 (``_detect_dialect``). ★ ``"ansi"`` 는 sqlglot 화이트리스트에
                 없어 ``ParseError`` — 사용 금지.

    Returns:
        ``DDLParseResult`` — 호출자가 모아서 Neo4j 업서트.
    """
    sql = path.read_text(encoding="utf-8")
    table_comments, column_comments = _extract_comment_on(sql)
    norm_sql, effective_dialect = _normalize_ddl(sql, dialect)

    result = DDLParseResult()
    seen_schemas: set[str] = set()
    seen_tables: set[tuple[str, str]] = set()

    statements = sqlglot.parse(norm_sql, dialect=effective_dialect)
    # 비수식 테이블의 소속 스키마 — SET search_path 가 구문 순서대로 갱신.
    current_schema = default_schema
    for stmt in statements:
        if stmt is None:
            continue
        if isinstance(stmt, (exp.Set, exp.Command)):
            current_schema = _extract_search_path(stmt) or current_schema
        elif isinstance(stmt, exp.Create):
            _handle_create(
                stmt, db, current_schema, table_comments, column_comments,
                result, seen_schemas, seen_tables,
            )
        elif isinstance(stmt, exp.Alter):
            _handle_alter(stmt, db, current_schema, result)
    return result


def _handle_create(
    stmt: exp.Create,
    db: str,
    default_schema: str,
    table_comments: dict[str, str],
    column_comments: dict[tuple[str, str], str],
    result: DDLParseResult,
    seen_schemas: set[str],
    seen_tables: set[tuple[str, str]],
) -> None:
    """CREATE 구문 처리 — kind 로 SCHEMA / TABLE / VIEW 분기.

    ``CREATE TABLE`` 과 컬럼목록 있는 ``CREATE VIEW`` 는 ``stmt.this`` 가 ``exp.Schema``
    (this=Table + expressions=ColumnDef). 컬럼목록 없는 평범한 ``CREATE VIEW v AS SELECT``
    는 ``stmt.this`` 가 ``exp.Table`` 직접 — 노드만 만들고 컬럼은 없음 (뷰 컬럼은 SELECT
    에서 옴 — 분석 범위 밖).
    """
    kind = (stmt.args.get("kind") or "").upper()
    if kind == "SCHEMA":
        _handle_create_schema(stmt, db, result, seen_schemas)
        return
    if kind not in ("TABLE", "VIEW"):
        return

    node = stmt.this
    if isinstance(node, exp.Schema):           # CREATE TABLE / 컬럼목록 있는 VIEW
        tab = node.this
        col_container: exp.Schema | None = node
    elif isinstance(node, exp.Table):          # 평범한 CREATE VIEW (컬럼목록 없음)
        tab = node
        col_container = None
    else:
        return
    if not isinstance(tab, exp.Table):
        return

    table_name = tab.name
    schema_name = tab.db or default_schema or ""

    # Schema (중복 제거)
    if schema_name and schema_name not in seen_schemas:
        seen_schemas.add(schema_name)
        result.schemas.append(Schema(db=db, name=schema_name))

    # Table — description = COMMENT ON 우선, 없으면 -- 주석 (출처는 description_source).
    raw_comment = _join_comments(stmt.comments)
    description, description_source = _resolve_description(
        table_comments.get(table_name.lower(), ""), raw_comment,
    )
    tkey = (schema_name, table_name)
    if tkey not in seen_tables:
        seen_tables.add(tkey)
        result.tables.append(Table(
            db=db,
            schema=schema_name,
            name=table_name,
            description=description,
            description_source=description_source,
            comment=raw_comment,
            table_type=kind,
        ))
        if schema_name:
            result.belongs_to_edges.append(BelongsTo(
                child_id=db_id(db, schema_name, table_name),
                parent_id=db_id(db, schema_name),
            ))

    # Columns + 테이블 레벨 PK + FK — 컬럼목록 있는 경우만 (평범한 VIEW 는 skip)
    if col_container is None:
        return
    table_fqn = db_id(db, schema_name, table_name)
    pk_cols = _table_pk_columns(col_container)
    for col_expr in col_container.expressions:
        if isinstance(col_expr, exp.ColumnDef):
            _handle_column(col_expr, table_name, table_fqn, pk_cols, column_comments, result)
    for fk_expr in col_container.find_all(exp.ForeignKey):
        _handle_inline_fk(fk_expr, db, schema_name, table_name, default_schema, result)


def _handle_create_schema(
    stmt: exp.Create,
    db: str,
    result: DDLParseResult,
    seen_schemas: set[str],
) -> None:
    """``CREATE SCHEMA x`` → Schema 노드 (중복 제거).

    sqlglot 은 ``CREATE SCHEMA oss_demo`` 의 이름을 ``stmt.this`` (``exp.Table``) 의
    ``db`` 에 담는다. search_path 는 안 건드림 — 그건 ``SET search_path`` 의 몫.
    """
    node = stmt.this
    name = ""
    if isinstance(node, exp.Table):
        name = node.db or node.name
    elif node is not None:
        name = getattr(node, "name", "") or ""
    if name and name not in seen_schemas:
        seen_schemas.add(name)
        result.schemas.append(Schema(db=db, name=name))


def _table_pk_columns(table_expr: exp.Schema) -> set[str]:
    """테이블 레벨 ``PRIMARY KEY (a, b)`` → PK 컬럼 이름 집합 (소문자).

    명명 ``CONSTRAINT pk_x PRIMARY KEY (...)`` 는 ``exp.Constraint`` 래퍼 안에
    있으므로 ``find_all`` 로 평면·명명 둘 다 잡는다 (컬럼 레벨 PK 는
    ColumnConstraint 라 여기 안 걸림 — 무관).
    """
    pk_cols: set[str] = set()
    for pk in table_expr.find_all(exp.PrimaryKey):
        for ident in pk.expressions:
            pk_cols.add(ident.name.lower())
    return pk_cols


def _handle_column(
    col: exp.ColumnDef,
    table_name: str,
    table_fqn: str,
    pk_cols: set[str],
    column_comments: dict[tuple[str, str], str],
    result: DDLParseResult,
) -> None:
    """ColumnDef → Column 노드 + HAS_COLUMN 엣지. 제약·DEFAULT·설명 추출.

    제약은 ``ColumnDef.constraints`` 의 각 ``ColumnConstraint.kind`` 로 판정
    (컬럼 레벨) + 테이블 레벨 ``pk_cols`` 합집합.
    """
    name = col.name
    dtype = col.args.get("kind")
    dtype_str = dtype.sql() if dtype is not None else "UNKNOWN"

    is_pk = name.lower() in pk_cols
    is_unique = False
    is_not_null = False
    default_value: str | None = None
    for cc in col.args.get("constraints") or []:
        ckind = getattr(cc, "kind", None)
        if isinstance(ckind, exp.PrimaryKeyColumnConstraint):
            is_pk = True
        elif isinstance(ckind, exp.UniqueColumnConstraint):
            is_unique = True
        elif isinstance(ckind, exp.NotNullColumnConstraint):
            if not ckind.args.get("allow_null"):
                is_not_null = True
        elif isinstance(ckind, exp.DefaultColumnConstraint):
            default_value = ckind.this.sql() if ckind.this is not None else None

    raw_comment = _join_comments(col.comments)
    description, description_source = _resolve_description(
        column_comments.get((table_name.lower(), name.lower()), ""), raw_comment,
    )
    col_fqn = fqn(table_fqn, name)
    result.columns.append(Column(
        id=col_fqn,
        name=name,
        dtype=dtype_str,
        nullable=not (is_not_null or is_pk),   # PK 는 항상 NOT NULL
        is_primary_key=is_pk,
        is_unique=is_unique,
        default_value=default_value,
        description=description,
        description_source=description_source,
        comment=raw_comment,
    ))
    result.has_column_edges.append(HasColumn(parent_id=table_fqn, child_id=col_fqn))


def _handle_inline_fk(
    fk_expr: exp.ForeignKey,
    db: str,
    from_schema: str,
    from_table: str,
    default_schema: str,
    result: DDLParseResult,
) -> None:
    """테이블 레벨 FK (``FOREIGN KEY`` 절 — 명명 ``CONSTRAINT`` 포함) → Fk 엣지.

    composite FK 지원 (from/to 컬럼 zip 1:1 매핑).
    """
    ref = fk_expr.args.get("reference")
    if not isinstance(ref, exp.Reference):
        return
    ref_table = ref.this
    if not isinstance(ref_table, exp.Schema):
        return
    target_tab = ref_table.this
    if not isinstance(target_tab, exp.Table):
        return

    to_schema = target_tab.db or default_schema or ""
    to_table = target_tab.name

    from_cols = [c.name for c in fk_expr.expressions if isinstance(c, exp.Identifier)]
    to_cols = [c.name for c in ref_table.expressions if isinstance(c, exp.Identifier)]
    if not from_cols or not to_cols or len(from_cols) != len(to_cols):
        return

    for from_col, to_col in zip(from_cols, to_cols):
        result.fk_edges.append(Fk(
            from_id=db_id(db, from_schema, from_table),
            to_id=db_id(db, to_schema, to_table),
            from_column=from_col,
            to_column=to_col,
        ))


def _handle_alter(
    stmt: exp.Alter,
    db: str,
    default_schema: str,
    result: DDLParseResult,
) -> None:
    """ALTER TABLE ADD CONSTRAINT FOREIGN KEY 처리."""
    table_expr = stmt.this
    if not isinstance(table_expr, exp.Table):
        return
    from_schema = table_expr.db or default_schema or ""
    from_table = table_expr.name

    for action in stmt.args.get("actions", []) or []:
        for fk_expr in action.find_all(exp.ForeignKey):
            _handle_inline_fk(fk_expr, db, from_schema, from_table, default_schema, result)
