"""DDL 파싱 — sqlglot 다중 방언.

CREATE TABLE / CREATE VIEW / ALTER TABLE ... FK 등을 파싱해
``(Schema, Table, Column, Fk, HasColumn, BelongsTo)`` 튜플 반환.

새 방언 추가 = sqlglot 자동 흡수 (코드 변경 X). 이상 케이스만 ``schema_resolver`` 검토.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import sqlglot.expressions as exp

from data_models.graph_edges import BelongsTo, Fk, HasColumn
from data_models.graph_nodes import Column, Schema, Table


@dataclass
class DDLParseResult:
    """단일 DDL 파일 파싱 결과."""
    schemas: list[Schema] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    fk_edges: list[Fk] = field(default_factory=list)
    has_column_edges: list[HasColumn] = field(default_factory=list)
    belongs_to_edges: list[BelongsTo] = field(default_factory=list)


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
                       parse_ddl_step 이 ``schema_resolver`` 호출로 결정.
        dialect: sqlglot dialect (``"oracle"`` / ``"postgres"`` / ``"mysql"`` 등).
                 ``None`` (기본) = sqlglot 가 자동 감지. ★ ``"ansi"`` 는 sqlglot 27.13
                 화이트리스트에 없어 ``ParseError`` 던지므로 **사용 금지**.

    Returns:
        ``DDLParseResult`` — 호출자가 모아서 Neo4j 업서트.
    """
    sql = path.read_text(encoding="utf-8")
    result = DDLParseResult()

    seen_schemas: set[str] = set()
    seen_tables: set[tuple[str, str]] = set()

    statements = sqlglot.parse(sql, dialect=dialect)
    for stmt in statements:
        if stmt is None:
            continue
        if isinstance(stmt, exp.Create):
            _handle_create(stmt, db, default_schema, result, seen_schemas, seen_tables)
        elif isinstance(stmt, exp.Alter):
            _handle_alter(stmt, db, default_schema, result)
    return result


def _handle_create(
    stmt: exp.Create,
    db: str,
    default_schema: str,
    result: DDLParseResult,
    seen_schemas: set[str],
    seen_tables: set[tuple[str, str]],
) -> None:
    """CREATE TABLE / VIEW 처리."""
    kind = stmt.args.get("kind", "").upper()
    if kind not in ("TABLE", "VIEW"):
        return

    table_expr = stmt.this if isinstance(stmt.this, exp.Schema) else stmt.this
    if not isinstance(table_expr, exp.Schema):
        return

    tab = table_expr.this  # exp.Table
    if not isinstance(tab, exp.Table):
        return

    table_name = tab.name
    schema_name = tab.db or default_schema or ""

    # Schema (중복 제거)
    if schema_name and schema_name not in seen_schemas:
        seen_schemas.add(schema_name)
        result.schemas.append(Schema(db=db, name=schema_name))

    # Table
    tkey = (schema_name, table_name)
    if tkey not in seen_tables:
        seen_tables.add(tkey)
        result.tables.append(Table(
            db=db,
            schema=schema_name,
            name=table_name,
            table_type=kind,
        ))
        if schema_name:
            result.belongs_to_edges.append(BelongsTo(
                table_fqn=f"{db}.{schema_name}.{table_name}",
                schema_fqn=f"{db}.{schema_name}",
            ))

    # Columns + inline FK
    table_fqn = f"{db}.{schema_name}.{table_name}"
    for col_expr in table_expr.expressions:
        if isinstance(col_expr, exp.ColumnDef):
            _handle_column(col_expr, db, schema_name, table_name, table_fqn, result)
        elif isinstance(col_expr, exp.ForeignKey):
            _handle_inline_fk(col_expr, db, schema_name, table_name, default_schema, result)


def _handle_column(
    col: exp.ColumnDef,
    db: str,
    schema: str,
    table: str,
    table_fqn: str,
    result: DDLParseResult,
) -> None:
    """ColumnDef → Column 노드 + HAS_COLUMN 엣지."""
    name = col.name
    dtype = col.args.get("kind")
    dtype_str = dtype.sql() if dtype is not None else "UNKNOWN"

    is_pk = any(isinstance(c, exp.PrimaryKeyColumnConstraint) for c in col.find_all(exp.ColumnConstraint))
    is_unique = any(isinstance(c, exp.UniqueColumnConstraint) for c in col.find_all(exp.ColumnConstraint))
    is_not_null = any(isinstance(c, exp.NotNullColumnConstraint) for c in col.find_all(exp.ColumnConstraint))

    col_fqn = f"{table_fqn}.{name}"
    result.columns.append(Column(
        fqn=col_fqn,
        name=name,
        dtype=dtype_str,
        nullable=not is_not_null,
        is_primary_key=is_pk,
        is_unique=is_unique,
    ))
    result.has_column_edges.append(HasColumn(table_fqn=table_fqn, column_fqn=col_fqn))


def _handle_inline_fk(
    fk_expr: exp.ForeignKey,
    db: str,
    from_schema: str,
    from_table: str,
    default_schema: str,
    result: DDLParseResult,
) -> None:
    """inline FK (CREATE TABLE 안의 FOREIGN KEY 절) → Fk 엣지 (composite 지원)."""
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

    # FK 컬럼 쌍 — composite FK 지원 (zip 으로 1:1 매핑)
    from_cols = [c.name for c in fk_expr.expressions if isinstance(c, exp.Identifier)]
    to_cols = [c.name for c in ref_table.expressions if isinstance(c, exp.Identifier)]
    if not from_cols or not to_cols or len(from_cols) != len(to_cols):
        return

    for from_col, to_col in zip(from_cols, to_cols):
        result.fk_edges.append(Fk(
            from_table_fqn=f"{db}.{from_schema}.{from_table}",
            to_table_fqn=f"{db}.{to_schema}.{to_table}",
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
        if isinstance(action, exp.AddConstraint):
            for fk_expr in action.find_all(exp.ForeignKey):
                _handle_inline_fk(fk_expr, db, from_schema, from_table, default_schema, result)
