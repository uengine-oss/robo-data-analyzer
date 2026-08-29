"""DDL 파싱 — sqlglot 다중 방언 + Oracle 정규화 + COMMENT ON.

CREATE TABLE / VIEW / ALTER TABLE ... FK 등을 파싱해
``(Schema, Table, Column, Fk, HasColumn, BelongsTo)`` 튜플 반환.

DDL = 구조의 진실 기준 (plan §3.1) — 컬럼 타입·제약·기본값·FK·설명을 결정론 추출:
- description = 정식 ``COMMENT ON``만 사용하며 ``description_source`` = "ddl".
- ``--`` / ``/* */`` 원문은 Table/Column.comment 에 보존하되 권위 description으로
  승격하지 않는다. 일반 주석에는 생성 provenance·운영 메모·낡은 설명이 섞일 수 있다.
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
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.tokens import Token, TokenType

from graph.schema.graph_edges import BelongsTo, Fk, HasColumn
from graph.schema.graph_nodes import Column, Schema, Table
from graph.schema.node_ids import sql_column_id, sql_schema_id, sql_table_id
from pipeline.models.sql_identifier import SqlIdentifierKey, sql_identifier_key


SchemaIdentity = tuple[SqlIdentifierKey, SqlIdentifierKey]
TableIdentity = tuple[SqlIdentifierKey, SqlIdentifierKey, SqlIdentifierKey]
ColumnIdentity = tuple[
    SqlIdentifierKey, SqlIdentifierKey, SqlIdentifierKey, SqlIdentifierKey,
]


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

# dialect 마커와 파싱 전 제거는 sqlglot token이 소유한다.

def _detect_dialect(sql: str) -> str | None:
    """Detect dialect from syntax tokens, excluding literals and quoted identifiers."""
    tokens = sqlglot.tokenize(sql)
    oracle = False
    postgres = False
    mysql = False
    for index, token in enumerate(tokens):
        if token.token_type in (TokenType.STRING, TokenType.IDENTIFIER):
            continue
        value = token.text.upper()
        following = tokens[index + 1].token_type if index + 1 < len(tokens) else None
        oracle = oracle or value in {"VARCHAR2", "NVARCHAR2", "TABLESPACE", "NOLOGGING"}
        oracle = oracle or (value == "NUMBER" and following == TokenType.L_PAREN)
        postgres = postgres or value in {"SERIAL", "BIGSERIAL", "BYTEA"}
        mysql = mysql or value == "AUTO_INCREMENT"
        mysql = mysql or (value == "ENGINE" and following == TokenType.EQ)
    for name, matched in (("oracle", oracle), ("postgres", postgres), ("mysql", mysql)):
        if matched:
            return name
    return None


def _normalize_ddl(sql: str, dialect: str | None) -> tuple[str, str | None]:
    """파싱 전처리 — COMMENT ON·물리옵션·BYTE 단위·``/`` 종결자 제거 + dialect 결정."""
    effective = dialect or _detect_dialect(sql)
    sql = _strip_comment_statements(sql, effective)  # identity/literal은 별도 AST가 소유
    if effective == "oracle":
        sql = _normalize_oracle_physical_syntax(sql, effective)
    sql = _strip_sqlplus_terminators(sql, effective)
    return sql, effective


def _identifier_ast_key(identifier: exp.Expression) -> SqlIdentifierKey:
    if not isinstance(identifier, exp.Identifier) or not identifier.name:
        raise ValueError(f"expected SQL Identifier, got {identifier!r}")
    return sql_identifier_key(identifier.name, bool(identifier.args.get("quoted")))


def _identifier_ast_parts(identifier: exp.Expression) -> tuple[str, bool]:
    if not isinstance(identifier, exp.Identifier) or not identifier.name:
        raise ValueError(f"expected SQL Identifier, got {identifier!r}")
    return identifier.name, bool(identifier.args.get("quoted"))


def _schema_key(
    identifier: object,
    default_schema: str,
    default_schema_quoted: bool = False,
) -> SqlIdentifierKey:
    if isinstance(identifier, exp.Identifier):
        return _identifier_ast_key(identifier)
    return sql_identifier_key(default_schema, default_schema_quoted)


def _table_identity(
    table: exp.Table,
    default_schema: str,
    default_schema_quoted: bool = False,
) -> TableIdentity:
    return (
        _schema_key(table.args.get("catalog"), "", False),
        _schema_key(table.args.get("db"), default_schema, default_schema_quoted),
        _identifier_ast_key(table.this),
    )


def _view_dependency_ids(
    expression: exp.Expression,
    db: str,
    default_catalog: str,
    default_catalog_quoted: bool,
    default_schema: str,
    default_schema_quoted: bool,
) -> tuple[str, ...]:
    """Return exact physical Table IDs from the CREATE VIEW query AST.

    ``Scope.sources`` distinguishes a physical ``exp.Table`` from a CTE or derived-query
    ``Scope``. Every nested scope is traversed so physical sources inside CTEs remain
    obligations, while the CTE aliases themselves never become database targets.
    """
    dependencies: set[str] = set()
    for scope in traverse_scope(expression):
        for source in scope.sources.values():
            if not isinstance(source, exp.Table):
                continue
            table_name, table_quoted = _identifier_ast_parts(source.this)
            catalog_identifier = source.args.get("catalog")
            if isinstance(catalog_identifier, exp.Identifier):
                catalog_name, catalog_quoted = _identifier_ast_parts(catalog_identifier)
            else:
                catalog_name, catalog_quoted = default_catalog, default_catalog_quoted
            schema_identifier = source.args.get("db")
            if isinstance(schema_identifier, exp.Identifier):
                schema_name, schema_quoted = _identifier_ast_parts(schema_identifier)
            else:
                schema_name, schema_quoted = default_schema, default_schema_quoted
            dependencies.add(sql_table_id(
                db,
                schema_name,
                table_name,
                schema_quoted=schema_quoted,
                table_quoted=table_quoted,
                catalog=catalog_name,
                catalog_quoted=catalog_quoted,
            ))
    return tuple(sorted(dependencies))


def _is_sqlplus_slash_terminator(sql: str, token: Token) -> bool:
    if token.token_type != TokenType.SLASH:
        return False
    line_start = sql.rfind("\n", 0, token.start) + 1
    line_end = sql.find("\n", token.end + 1)
    if line_end < 0:
        line_end = len(sql)
    return sql[line_start:line_end].strip() == "/"


def _strip_sqlplus_terminators(sql: str, dialect: str | None) -> str:
    """Remove standalone SQL*Plus slash tokens, never slash text inside a literal."""
    intervals = [
        (token.start, token.end)
        for token in sqlglot.tokenize(sql, dialect=dialect)
        if _is_sqlplus_slash_terminator(sql, token)
    ]
    return _remove_source_intervals(sql, intervals)


def _statement_token_groups(sql: str, dialect: str | None) -> list[list[Token]]:
    """Return exact sqlglot-token statement groups, treating SQL*Plus ``/`` as a separator."""
    groups: list[list[Token]] = []
    current: list[Token] = []
    for token in sqlglot.tokenize(sql, dialect=dialect):
        if _is_sqlplus_slash_terminator(sql, token):
            if current:
                groups.append(current)
                current = []
            continue
        current.append(token)
        if token.token_type == TokenType.SEMICOLON:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _strip_comment_statements(sql: str, dialect: str | None) -> str:
    """Remove complete COMMENT statements without inspecting or rewriting their literals."""
    intervals = [
        (tokens[0].start, tokens[-1].end + 1)
        for tokens in _statement_token_groups(sql, dialect)
        if tokens and tokens[0].token_type == TokenType.COMMENT
    ]
    for start, end in reversed(intervals):
        sql = sql[:start] + sql[end:]
    return sql


def _remove_source_intervals(sql: str, intervals: list[tuple[int, int]]) -> str:
    """Remove non-overlapping inclusive source intervals from right to left."""
    for start, end in sorted(set(intervals), reverse=True):
        sql = sql[:start] + sql[end + 1:]
    return sql


def _schema_close_index(tokens: list[Token]) -> int | None:
    """Return the outer CREATE TABLE column-list closing parenthesis token index."""
    depth = 0
    opened = False
    for index, token in enumerate(tokens):
        if token.token_type == TokenType.L_PAREN:
            depth += 1
            opened = True
        elif token.token_type == TokenType.R_PAREN and opened:
            depth -= 1
            if depth == 0:
                return index
    return None


def _normalize_oracle_physical_syntax(sql: str, dialect: str | None) -> str:
    """Remove only structural Oracle DDL syntax identified by sqlglot tokens.

    STRING and quoted IDENTIFIER tokens are never inspected by text regexes. VARCHAR length
    units are removed only from the token shape ``VARCHAR*(number BYTE|CHAR)``. Table physical
    options are removed only after the outer CREATE TABLE column list.
    """
    intervals: list[tuple[int, int]] = []
    length_types = {"VARCHAR", "VARCHAR2", "NVARCHAR", "NVARCHAR2"}
    option_with_value = {"TABLESPACE", "PCTFREE", "PCTUSED", "INITRANS", "MAXTRANS"}

    for tokens in _statement_token_groups(sql, dialect):
        for index in range(len(tokens) - 4):
            type_token, left, size, unit, right = tokens[index:index + 5]
            if (
                type_token.text.upper() in length_types
                and left.token_type == TokenType.L_PAREN
                and size.token_type == TokenType.NUMBER
                and unit.text.upper() in {"BYTE", "CHAR"}
                and unit.token_type != TokenType.IDENTIFIER
                and right.token_type == TokenType.R_PAREN
            ):
                intervals.append((unit.start, unit.end))

        if len(tokens) < 3 or tokens[0].token_type != TokenType.CREATE:
            continue
        first_paren = next(
            (index for index, token in enumerate(tokens)
             if token.token_type == TokenType.L_PAREN),
            len(tokens),
        )
        if not any(
            token.token_type == TokenType.TABLE for token in tokens[:first_paren]
        ):
            continue
        close_index = _schema_close_index(tokens)
        if close_index is None:
            continue
        index = close_index + 1
        while index < len(tokens):
            token = tokens[index]
            option = token.text.upper()
            if option in option_with_value and token.token_type != TokenType.IDENTIFIER:
                if index + 1 >= len(tokens):
                    raise ValueError(f"Oracle {option} option is missing its value")
                intervals.append((token.start, tokens[index + 1].end))
                index += 2
                continue
            if option == "STORAGE" and token.token_type != TokenType.IDENTIFIER:
                if index + 1 >= len(tokens) or tokens[index + 1].token_type != TokenType.L_PAREN:
                    raise ValueError("Oracle STORAGE option is missing its parenthesized value")
                depth = 0
                end_index = None
                for cursor in range(index + 1, len(tokens)):
                    if tokens[cursor].token_type == TokenType.L_PAREN:
                        depth += 1
                    elif tokens[cursor].token_type == TokenType.R_PAREN:
                        depth -= 1
                        if depth == 0:
                            end_index = cursor
                            break
                if end_index is None:
                    raise ValueError("Oracle STORAGE option has an unclosed parenthesis")
                intervals.append((token.start, tokens[end_index].end))
                index = end_index + 1
                continue
            if option in {"LOGGING", "NOLOGGING"} and token.token_type != TokenType.IDENTIFIER:
                intervals.append((token.start, token.end))
            index += 1

    return _remove_source_intervals(sql, intervals)


def _metadata_statements(
    sql: str, dialect: str | None,
) -> list[tuple[exp.Expression, list[Token]]]:
    """Parse SET/COMMENT from exact source token boundaries.

    Oracle physical CREATE options are normalized separately. They must not rewrite COMMENT
    literals. sqlglot tokens provide exact statement boundaries, including semicolons inside
    string literals, and sqlglot AST remains the owner of identifier spelling and quoted bits.
    """
    statements: list[tuple[exp.Expression, list[Token]]] = []
    for tokens in _statement_token_groups(sql, dialect):
        first = tokens[0]
        if first.token_type not in (TokenType.SET, TokenType.COMMENT):
            continue
        source = sql[first.start:tokens[-1].end + 1]
        parsed = sqlglot.parse_one(source, dialect=dialect)
        if not isinstance(parsed, (exp.Set, exp.Comment)):
            raise ValueError(f"metadata statement parsed as {type(parsed).__name__}")
        statements.append((parsed, tokens))
    return statements


def _parsed_statement_groups(
    sql: str, dialect: str | None,
) -> list[tuple[exp.Expression, list[Token]]]:
    token_groups = _statement_token_groups(sql, dialect)
    parsed = [statement for statement in sqlglot.parse(sql, dialect=dialect)
              if statement is not None]
    if len(parsed) != len(token_groups):
        raise ValueError(
            "SQL AST/token statement population mismatch: "
            f"ast={len(parsed)} tokens={len(token_groups)}"
        )
    return list(zip(parsed, token_groups))


# ── search_path / 일반주석 ────────────────────────────────────

def _extract_search_path(tokens: list[Token]) -> tuple[str, bool] | None:
    """Return the first search_path identifier from exact source tokens."""
    for index, token in enumerate(tokens):
        if token.text.lower() != "search_path":
            continue
        if index + 2 >= len(tokens):
            return None
        operator = tokens[index + 1]
        if operator.token_type != TokenType.EQ and operator.text.upper() != "TO":
            return None
        value = tokens[index + 2]
        if value.token_type in (TokenType.COMMA, TokenType.SEMICOLON):
            return None
        return value.text, value.token_type == TokenType.IDENTIFIER
    return None


def _extract_comment_on(
    sql: str,
    *,
    dialect: str | None,
    default_schema: str,
) -> tuple[dict[TableIdentity, str], dict[ColumnIdentity, str]]:
    """Extract formal comments from sqlglot AST with exact qualified identity."""
    table_comments: dict[TableIdentity, str] = {}
    column_comments: dict[ColumnIdentity, str] = {}
    current_schema = (default_schema, False)
    for stmt, tokens in _metadata_statements(sql, dialect):
        if isinstance(stmt, exp.Set):
            current_schema = _extract_search_path(tokens) or current_schema
            continue
        target = stmt.this
        literal = stmt.args.get("expression")
        if not isinstance(literal, exp.Literal) or not literal.is_string:
            raise ValueError("COMMENT ON requires a string literal")
        kind = str(stmt.args.get("kind") or "").upper()
        if kind in ("TABLE", "VIEW"):
            if not isinstance(target, exp.Table):
                raise ValueError("COMMENT ON TABLE/VIEW target must be a Table AST")
            table_comments[_table_identity(target, *current_schema)] = literal.this
        elif kind == "COLUMN":
            if not isinstance(target, exp.Column):
                raise ValueError("COMMENT ON COLUMN target must be a Column AST")
            table_identifier = target.args.get("table")
            if not isinstance(table_identifier, exp.Identifier):
                raise ValueError("COMMENT ON COLUMN target must include a table")
            table_key = (
                _schema_key(target.args.get("catalog"), "", False),
                _schema_key(target.args.get("db"), *current_schema),
                _identifier_ast_key(table_identifier),
            )
            column_comments[(*table_key, _identifier_ast_key(target.this))] = literal.this
    return table_comments, column_comments


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
    """정식 ``COMMENT ON``만 권위 description으로 인정한다.

    Returns ``(description, description_source)``:
    일반 주석은 별도 ``comment`` 필드에 이미 보존된다. provenance나 활성 코드와 충돌하는
    주석을 schema const로 승격하지 않기 위해 여기서는 fallback으로 쓰지 않는다.
    """
    del raw_comment
    description = formal
    return description, ("ddl" if description else "")


# ── 파일 진입점 ───────────────────────────────────────────────

def parse_ddl_file(
    path: Path,
    *,
    db: str,
    default_schema: str = "",
    dialect: str | None = None,
    sealed_source_bytes: bytes | None = None,
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
    sql = (
        path.read_text(encoding="utf-8")
        if sealed_source_bytes is None
        else sealed_source_bytes.decode("utf-8", errors="strict")
    )
    norm_sql, effective_dialect = _normalize_ddl(sql, dialect)
    table_comments, column_comments = _extract_comment_on(
        sql, dialect=effective_dialect, default_schema=default_schema,
    )

    result = DDLParseResult()
    seen_schemas: set[SchemaIdentity] = set()
    seen_tables: set[TableIdentity] = set()
    unique_keys_by_table: dict[
        TableIdentity, set[frozenset[SqlIdentifierKey]]
    ] = {}

    # 비수식 테이블의 소속 스키마 — SET search_path 가 구문 순서대로 갱신.
    current_schema = (default_schema, False)
    for stmt, tokens in _parsed_statement_groups(norm_sql, effective_dialect):
        if isinstance(stmt, (exp.Set, exp.Command)):
            current_schema = _extract_search_path(tokens) or current_schema
        elif isinstance(stmt, exp.Create):
            _handle_create(
                stmt, db, *current_schema, table_comments, column_comments,
                result, seen_schemas, seen_tables, unique_keys_by_table,
            )
        elif isinstance(stmt, exp.Alter):
            _handle_alter(
                stmt, db, *current_schema, result, unique_keys_by_table,
            )
    return result


def _handle_create(
    stmt: exp.Create,
    db: str,
    default_schema: str,
    default_schema_quoted: bool,
    table_comments: dict[TableIdentity, str],
    column_comments: dict[ColumnIdentity, str],
    result: DDLParseResult,
    seen_schemas: set[SchemaIdentity],
    seen_tables: set[TableIdentity],
    unique_keys_by_table: dict[
        TableIdentity, set[frozenset[SqlIdentifierKey]]
    ],
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

    table_name, table_name_quoted = _identifier_ast_parts(tab.this)
    catalog_identifier = tab.args.get("catalog")
    if isinstance(catalog_identifier, exp.Identifier):
        catalog_name, catalog_name_quoted = _identifier_ast_parts(catalog_identifier)
    else:
        catalog_name, catalog_name_quoted = "", False
    schema_identifier = tab.args.get("db")
    if isinstance(schema_identifier, exp.Identifier):
        schema_name, schema_name_quoted = _identifier_ast_parts(schema_identifier)
    else:
        schema_name, schema_name_quoted = default_schema or "", default_schema_quoted
    table_key = _table_identity(tab, default_schema, default_schema_quoted)
    schema_key = table_key[:2]

    # Schema (중복 제거)
    if schema_name and schema_key not in seen_schemas:
        seen_schemas.add(schema_key)
        result.schemas.append(Schema(
            db=db, catalog=catalog_name, catalog_quoted=catalog_name_quoted,
            name=schema_name, name_quoted=schema_name_quoted,
        ))

    # Table — 정식 COMMENT ON만 description. 일반 주석은 comment에만 보존한다.
    raw_comment = _join_comments(stmt.comments)
    description, description_source = _resolve_description(
        table_comments.get(table_key, ""), raw_comment,
    )
    if table_key not in seen_tables:
        seen_tables.add(table_key)
        # VIEW 정의 문자열은 감사/표시용이다. relationship의 권위 입력은 같은 AST scope에서
        # physical source만 뽑은 canonical dependency ID다.
        view_definition = ""
        view_dependency_ids: tuple[str, ...] = ()
        if kind == "VIEW" and stmt.args.get("expression") is not None:
            view_expression = stmt.args["expression"]
            view_definition = view_expression.sql()
            view_dependency_ids = _view_dependency_ids(
                view_expression, db, catalog_name, catalog_name_quoted,
                schema_name, schema_name_quoted,
            )
        table = Table(
            db=db,
            catalog=catalog_name,
            catalog_quoted=catalog_name_quoted,
            schema=schema_name,
            name=table_name,
            description=description,
            description_source=description_source,
            comment=raw_comment,
            table_type=kind,
            view_definition=view_definition,
            schema_quoted=schema_name_quoted,
            name_quoted=table_name_quoted,
            view_dependency_ids=view_dependency_ids,
        )
        result.tables.append(table)
        if schema_name:
            result.belongs_to_edges.append(BelongsTo(
                child_id=table.id,
                parent_id=sql_schema_id(
                    db, schema_name, schema_name_quoted,
                    catalog=catalog_name, catalog_quoted=catalog_name_quoted,
                ),
            ))

    # Columns + 테이블 레벨 PK + FK — 컬럼목록 있는 경우만 (평범한 VIEW 는 skip)
    if col_container is None:
        return
    table_fqn = sql_table_id(
        db, schema_name, table_name,
        schema_quoted=schema_name_quoted,
        table_quoted=table_name_quoted,
        catalog=catalog_name,
        catalog_quoted=catalog_name_quoted,
    )
    pk_cols = _table_pk_columns(col_container)
    unique_keys = _declared_unique_keys(col_container)
    unique_keys_by_table[table_key] = unique_keys
    for col_expr in col_container.expressions:
        if isinstance(col_expr, exp.ColumnDef):
            _handle_column(
                col_expr, table_key, table_fqn, pk_cols, column_comments, result,
            )
    for fk_expr in col_container.find_all(exp.ForeignKey):
        _handle_inline_fk(
            fk_expr, db, catalog_name, catalog_name_quoted,
            schema_name, schema_name_quoted, table_name,
            table_name_quoted, result, unique_keys,
        )


def _handle_create_schema(
    stmt: exp.Create,
    db: str,
    result: DDLParseResult,
    seen_schemas: set[SchemaIdentity],
) -> None:
    """``CREATE SCHEMA x`` → Schema 노드 (중복 제거).

    sqlglot 은 ``CREATE SCHEMA oss_demo`` 의 이름을 ``stmt.this`` (``exp.Table``) 의
    ``db`` 에 담는다. search_path 는 안 건드림 — 그건 ``SET search_path`` 의 몫.
    """
    node = stmt.this
    name = ""
    catalog = ""
    if isinstance(node, exp.Table):
        name = node.db or node.name
        catalog = node.catalog or ""
    elif node is not None:
        name = getattr(node, "name", "") or ""
    identifier = None
    if isinstance(node, exp.Table):
        identifier = node.args.get("db") or node.args.get("this")
    schema_identity = (
        _identifier_ast_key(identifier)
        if isinstance(identifier, exp.Identifier)
        else sql_identifier_key(name, False)
    )
    catalog_identifier = node.args.get("catalog") if isinstance(node, exp.Table) else None
    catalog_identity = (
        _identifier_ast_key(catalog_identifier)
        if isinstance(catalog_identifier, exp.Identifier)
        else sql_identifier_key(catalog, False)
    )
    identity = (catalog_identity, schema_identity)
    if name and identity not in seen_schemas:
        seen_schemas.add(identity)
        quoted = bool(
            isinstance(identifier, exp.Identifier) and identifier.args.get("quoted")
        )
        catalog_quoted = bool(
            isinstance(catalog_identifier, exp.Identifier)
            and catalog_identifier.args.get("quoted")
        )
        result.schemas.append(Schema(
            db=db, catalog=catalog, catalog_quoted=catalog_quoted,
            name=name, name_quoted=quoted,
        ))


def _table_pk_columns(table_expr: exp.Schema) -> set[SqlIdentifierKey]:
    """Return exact identities named by table-level ``PRIMARY KEY`` constraints.

    명명 ``CONSTRAINT pk_x PRIMARY KEY (...)`` 는 ``exp.Constraint`` 래퍼 안에
    있으므로 ``find_all`` 로 평면·명명 둘 다 잡는다 (컬럼 레벨 PK 는
    ColumnConstraint 라 여기 안 걸림 — 무관).
    """
    pk_cols: set[SqlIdentifierKey] = set()
    for pk in table_expr.find_all(exp.PrimaryKey):
        for ident in pk.expressions:
            pk_cols.add(_identifier_ast_key(ident))
    return pk_cols


def _declared_unique_keys(
    table_expr: exp.Schema,
) -> set[frozenset[SqlIdentifierKey]]:
    """Return exact local column sets declared unique by PRIMARY KEY or UNIQUE.

    Cardinality belongs to the complete constraint, not to each member: a
    single column from ``PRIMARY KEY (tenant_id, id)`` is therefore not a
    unique key. Column-level constraints are represented separately by
    sqlglot and become singleton keys.
    """
    keys: set[frozenset[SqlIdentifierKey]] = set()

    for pk in table_expr.find_all(exp.PrimaryKey):
        names = frozenset(
            _identifier_ast_key(ident) for ident in pk.expressions if ident.name
        )
        if names:
            keys.add(names)

    for unique in table_expr.find_all(exp.UniqueColumnConstraint):
        target = unique.this
        if isinstance(target, exp.Schema):
            names = frozenset(
                _identifier_ast_key(ident)
                for ident in target.expressions if ident.name
            )
            if names:
                keys.add(names)

    for col in table_expr.expressions:
        if not isinstance(col, exp.ColumnDef):
            continue
        for constraint in col.args.get("constraints") or []:
            kind = getattr(constraint, "kind", None)
            if isinstance(kind, (exp.PrimaryKeyColumnConstraint, exp.UniqueColumnConstraint)):
                keys.add(frozenset((_identifier_ast_key(col.this),)))

    return keys


def _handle_column(
    col: exp.ColumnDef,
    table_identity: TableIdentity,
    table_fqn: str,
    pk_cols: set[SqlIdentifierKey],
    column_comments: dict[ColumnIdentity, str],
    result: DDLParseResult,
) -> None:
    """ColumnDef → Column 노드 + HAS_COLUMN 엣지. 제약·DEFAULT·설명 추출.

    제약은 ``ColumnDef.constraints`` 의 각 ``ColumnConstraint.kind`` 로 판정
    (컬럼 레벨) + 테이블 레벨 ``pk_cols`` 합집합.
    """
    name = col.name
    identifier = col.this
    name_quoted = bool(
        isinstance(identifier, exp.Identifier) and identifier.args.get("quoted")
    )
    column_key = _identifier_ast_key(identifier)
    dtype = col.args.get("kind")
    dtype_str = dtype.sql() if dtype is not None else "UNKNOWN"

    is_pk = column_key in pk_cols
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
        column_comments.get((*table_identity, column_key), ""), raw_comment,
    )
    col_fqn = sql_column_id(table_fqn, name, name_quoted)
    result.columns.append(Column(
        id=col_fqn,
        name=name,
        dtype=dtype_str,
        name_quoted=name_quoted,
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
    from_catalog: str,
    from_catalog_quoted: bool,
    from_schema: str,
    from_schema_quoted: bool,
    from_table: str,
    from_table_quoted: bool,
    result: DDLParseResult,
    source_unique_keys: set[frozenset[SqlIdentifierKey]],
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

    to_table, to_table_quoted = _identifier_ast_parts(target_tab.this)
    target_catalog_identifier = target_tab.args.get("catalog")
    if isinstance(target_catalog_identifier, exp.Identifier):
        to_catalog, to_catalog_quoted = _identifier_ast_parts(target_catalog_identifier)
    else:
        to_catalog, to_catalog_quoted = from_catalog, from_catalog_quoted
    target_schema_identifier = target_tab.args.get("db")
    if isinstance(target_schema_identifier, exp.Identifier):
        to_schema, to_schema_quoted = _identifier_ast_parts(target_schema_identifier)
    else:
        to_schema, to_schema_quoted = from_schema, from_schema_quoted

    from_identifiers = [
        column for column in fk_expr.expressions
        if isinstance(column, exp.Identifier)
    ]
    to_identifiers = [
        column for column in ref_table.expressions
        if isinstance(column, exp.Identifier)
    ]
    from_cols = [column.name for column in from_identifiers]
    to_cols = [column.name for column in to_identifiers]
    if not from_cols or not to_cols or len(from_cols) != len(to_cols):
        return

    fk_type = (
        "one_to_one"
        if frozenset(
            _identifier_ast_key(column) for column in from_identifiers
        ) in source_unique_keys
        else "many_to_one"
    )
    for from_col, to_col in zip(from_cols, to_cols):
        result.fk_edges.append(Fk(
            from_id=sql_table_id(
                db, from_schema, from_table,
                schema_quoted=from_schema_quoted,
                table_quoted=from_table_quoted,
                catalog=from_catalog,
                catalog_quoted=from_catalog_quoted,
            ),
            to_id=sql_table_id(
                db, to_schema, to_table,
                schema_quoted=to_schema_quoted,
                table_quoted=to_table_quoted,
                catalog=to_catalog,
                catalog_quoted=to_catalog_quoted,
            ),
            from_column=from_col,
            to_column=to_col,
            fk_type=fk_type,
            is_candidate=False,
        ))


def _handle_alter(
    stmt: exp.Alter,
    db: str,
    default_schema: str,
    default_schema_quoted: bool,
    result: DDLParseResult,
    unique_keys_by_table: dict[
        TableIdentity, set[frozenset[SqlIdentifierKey]]
    ],
) -> None:
    """ALTER TABLE ADD CONSTRAINT FOREIGN KEY 처리."""
    table_expr = stmt.this
    if not isinstance(table_expr, exp.Table):
        return
    from_table, from_table_quoted = _identifier_ast_parts(table_expr.this)
    catalog_identifier = table_expr.args.get("catalog")
    if isinstance(catalog_identifier, exp.Identifier):
        from_catalog, from_catalog_quoted = _identifier_ast_parts(catalog_identifier)
    else:
        from_catalog, from_catalog_quoted = "", False
    schema_identifier = table_expr.args.get("db")
    if isinstance(schema_identifier, exp.Identifier):
        from_schema, from_schema_quoted = _identifier_ast_parts(schema_identifier)
    else:
        from_schema, from_schema_quoted = default_schema or "", default_schema_quoted
    unique_keys = unique_keys_by_table.get(
        _table_identity(table_expr, default_schema, default_schema_quoted), set(),
    )

    for action in stmt.args.get("actions", []) or []:
        for fk_expr in action.find_all(exp.ForeignKey):
            _handle_inline_fk(
                fk_expr, db, from_catalog, from_catalog_quoted,
                from_schema, from_schema_quoted,
                from_table, from_table_quoted,
                result, unique_keys,
            )
