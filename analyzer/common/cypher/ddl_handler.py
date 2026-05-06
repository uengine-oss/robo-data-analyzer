"""DDL 처리 — 파일 목록 + sqlglot AST 파싱 + Neo4j 반영 + 처리 루프.

책임 (한 도메인 = 한 파일 — DDL 처리 흐름):
  1. DDL 디렉토리 스캔 + 이름 케이스 변환  (Section 1)
  2. CREATE TABLE / ALTER TABLE / COMMENT ON 다중 방언 AST 파싱  (Section 2)
  3. 파싱 결과 → Neo4j UNWIND 배치 저장  (Section 3)
  4. 파일별 처리 루프 + 진행률 emit  (Section 4)

호출자 (framework/dbms phase_ddl) 가 본 모듈의 `run_ddl_processing` 만 호출.

파서 (Section 2):
  - sqlglot 기반. Oracle / PostgreSQL / MySQL / TSQL / Snowflake 등 다중 방언 지원.
  - Oracle SQL*Plus 슬래시(`/`) 종결자, TABLESPACE/STORAGE/LOB 등 저장 옵션,
    한 줄 다중 컬럼, 따옴표 식별자, ALTER TABLE 제약, COMMENT ON 모두 자동 처리.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple

import sqlglot

from config.settings import settings
from sqlglot import exp

from client.neo4j_client import Neo4jClient
from util.encoding_reader import read_text_with_fallback
from util.logger import log_process
from util.stream_emitter import (
    emit_data,
    emit_user_message,
    emit_phase_event,
)
from util.code_text_utils import (
    parse_table_identifier,
    calculate_code_token,
)
from analyzer.common.cypher.graph_writer import (
    SCHEMA_MERGE_QUERY,
    TABLE_MERGE_QUERY,
    BELONGS_TO_QUERY,
    COLUMN_MERGE_QUERY,
    HAS_COLUMN_QUERY,
    FK_REF_TABLE_QUERY,
    FK_TO_TABLE_QUERY,
    FK_TO_COLUMN_QUERY,
    DATASOURCE_CREATE_QUERY,
    DATASOURCE_SCHEMA_QUERY,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Section 1: 파일 목록 + 이름 케이스 변환
# ═══════════════════════════════════════════════════════════════════════


def apply_name_case(name: str, name_case: str) -> str:
    """메타데이터 대소문자 변환 적용."""
    if not name:
        return name
    if name_case == "uppercase":
        return name.upper()
    elif name_case == "lowercase":
        return name.lower()
    return name


def list_ddl_files(orchestrator: Any) -> List[str]:
    """DDL 파일 목록 조회.

    DDL 디렉토리가 없거나 파일이 없으면 빈 리스트 반환 (경고 처리, 에러 아님).
    """
    ddl_dir = orchestrator.dirs.get("ddl", "")
    if not ddl_dir:
        log_process("ANALYZE", "DDL", "DDL 디렉토리 설정 없음 - DDL 처리 생략")
        return []
    if not os.path.isdir(ddl_dir):
        log_process("ANALYZE", "DDL", f"DDL 디렉토리 없음: {ddl_dir} - DDL 처리 생략")
        return []
    try:
        files = sorted(
            f for f in os.listdir(ddl_dir)
            if os.path.isfile(os.path.join(ddl_dir, f))
        )
        if not files:
            log_process("ANALYZE", "DDL", f"DDL 디렉토리에 파일 없음: {ddl_dir} - DDL 처리 생략")
            return []
        log_process("ANALYZE", "DDL", f"DDL 파일 발견: {len(files)}개")
        return files
    except OSError as e:  # noqa: SILENT_FAIL — DDL 디렉토리 읽기 실패는 빈 결과로 진행 (DDL 없는 시나리오와 동일)
        log_process("ANALYZE", "DDL", f"DDL 디렉토리 읽기 실패: {ddl_dir} - {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════
# Section 2: DDL 파서 — sqlglot AST + COMMENT regex hybrid
# ═══════════════════════════════════════════════════════════════════════
#
# 설계 원칙
#   - CREATE / ALTER TABLE     → sqlglot AST (다중 방언, 정밀 추출)
#   - COMMENT ON TABLE/COLUMN  → 결정론 정규식 (형식 표준 + 한글·특수문자 robust)
#   - 입력 정규화는 sqlglot이 모르는 Oracle export 절·길이 단위·잡문 제거에만 사용
#
# 단계
#   parse(content, dialect=None)
#     ├ Stage 1: _split_statements   — 종결자/주석/따옴표 인지 statement 분리
#     ├ Stage 2: _normalize_chunk    — SQL 시작 trim + 옵션 절 제거 + 길이 단위 표준화
#     ├ Stage 3: _dispatch_statement — CREATE / ALTER 라우팅 (sqlglot AST)
#     ├ Stage 4: _extract_*          — AST → dict 변환 (테이블/컬럼/PK/FK)
#     └ Stage 5: _apply_comments     — 원문에서 COMMENT ON 직접 매칭 → 적용
#
# 출력 형식 (호환성 유지)
#   {"analysis": [
#       {"table":      {"schema", "name", "comment", "table_type"},
#        "columns":    [{"name", "dtype", "nullable", "comment", "primary_key", ...}],
#        "foreignKeys":[{"column", "ref": "schema.table.col"}],
#        "primaryKeys":["col1", ...]}
#   ]}


class DDLParser:
    """다중 방언 DDL 파서. 모든 정규식·유틸이 클래스 내부에 캡슐화됨."""

    DEFAULT_DIALECT = "oracle"

    # ── 정규식 (단일 책임별로 1개씩) ─────────────────────────

    # 자동 방언 감지 — 가장 특징적인 마커만
    _DIALECT_MARKERS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
        ("oracle",   re.compile(r"\bTABLESPACE\b|\bPCTFREE\b|\bSTORAGE\s*\(|\bNVARCHAR2\b|\bVARCHAR2\b|\(\d+\s+BYTE\)", re.IGNORECASE)),
        ("postgres", re.compile(r"\bSERIAL\b|\bBIGSERIAL\b|\bJSONB\b|::\w+", re.IGNORECASE)),
        ("mysql",    re.compile(r"\bENGINE\s*=|\bAUTO_INCREMENT\b|\bUNSIGNED\b", re.IGNORECASE)),
        ("tsql",     re.compile(r"\bIDENTITY\s*\(|\bNVARCHAR\s*\(\s*MAX\b|\[[^\]]+\]", re.IGNORECASE)),
    )

    # SQL 첫 키워드 — chunk 앞의 비-SQL 잡문 trim 용
    _SQL_START_RE = re.compile(
        r"\b(CREATE|ALTER|DROP|TRUNCATE|COMMENT|INSERT|UPDATE|DELETE|"
        r"GRANT|REVOKE|MERGE|RENAME|ANALYZE)\b",
        re.IGNORECASE,
    )

    # Oracle/DBA 저장 옵션 (CREATE TABLE 본문 ) 다음 ~ statement 끝)
    # 분석에 무가치한 물리 메타이며 sqlglot이 미지원하여 fallback Command가 되므로 제거.
    _STORAGE_OPTIONS_RE = re.compile(
        r"(\)\s*)"
        r"(?:TABLESPACE|PCTFREE|PCTUSED|INITRANS|MAXTRANS|STORAGE\s*\(|"
        r"LOGGING|NOLOGGING|COMPRESS|NOCOMPRESS|PARALLEL|NOPARALLEL|"
        r"MONITORING|NOMONITORING|CACHE|NOCACHE|RESULT_CACHE|FLASHBACK|"
        r"INMEMORY|NO\s+INMEMORY|ROW\s+(?:STORE|MOVEMENT)|"
        r"SEGMENT\s+CREATION|ORGANIZATION\s+(?:HEAP|INDEX)|ENABLE|DISABLE|"
        r"LOB\s*\()"
        r".*?\Z",
        re.IGNORECASE | re.DOTALL,
    )

    # Oracle 길이 단위: VARCHAR(20 BYTE) → VARCHAR(20) (sqlglot 미지원)
    _BYTE_CHAR_LEN_RE = re.compile(r"\(\s*(\d+)\s+(?:BYTE|CHAR)\s*\)", re.IGNORECASE)

    # 블록 주석 (라인 인덱스 보존 위해 동일 길이 공백 치환)
    _BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

    # COMMENT ON TABLE/COLUMN — 표준 형식, 한글·특수문자 robust
    _COMMENT_ON_TABLE_RE = re.compile(
        r"COMMENT\s+ON\s+TABLE\s+"
        r'(?:"(?P<schema>[^"]+)"\.)?"(?P<name>[^"]+)"\s+'
        r"IS\s+'(?P<body>(?:[^']|'')*)'",
        re.IGNORECASE | re.DOTALL,
    )
    _COMMENT_ON_COLUMN_RE = re.compile(
        r"COMMENT\s+ON\s+COLUMN\s+"
        r'(?:"(?P<schema>[^"]+)"\.)?"(?P<table>[^"]+)"\."(?P<col>[^"]+)"\s+'
        r"IS\s+'(?P<body>(?:[^']|'')*)'",
        re.IGNORECASE | re.DOTALL,
    )

    # ════ Public API ════════════════════════════════════════════

    def parse(self, ddl_content: str, dialect: Optional[str] = None) -> Dict[str, Any]:
        """DDL 텍스트 → 테이블/컬럼/PK/FK/comment 구조."""
        dialect = dialect or self._detect_dialect(ddl_content)
        tables: Dict[str, Dict[str, Any]] = {}

        # Stage 1~4: CREATE/ALTER TABLE → AST → dict
        for chunk in self._split_statements(ddl_content):
            normalized = self._normalize_chunk(chunk)
            for stmt in self._parse_chunk(normalized, dialect):
                self._dispatch_statement(stmt, tables, dialect)

        # Stage 5: COMMENT ON 적용 (sqlglot보다 regex가 한글·특수문자에 robust)
        self._apply_comments(ddl_content, tables)

        logger.info(f"[DDL_PARSER] 파싱 완료: {len(tables)}개 테이블 (dialect={dialect})")
        return {"analysis": list(tables.values())}

    # ════ Stage 1: statement 분리 ═══════════════════════════════

    @classmethod
    def _split_statements(cls, ddl: str) -> List[str]:
        """DDL → statement 리스트.

        종결자: 표준 SQL `;` + Oracle SQL*Plus 자체 라인 단독 `/`
        인지: `'...'` (escape `''` 포함), `"..."`, `--` 라인 주석, `/* ... */` 블록 주석
        """
        out: List[str] = []
        buf: List[str] = []
        in_sq = in_dq = False
        in_line_comment = in_block_comment = False
        n = len(ddl)
        i = 0

        def flush() -> None:
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf.clear()

        while i < n:
            ch = ddl[i]
            nxt = ddl[i + 1] if i + 1 < n else ""

            if in_line_comment:
                buf.append(ch)
                if ch == "\n":
                    in_line_comment = False
                i += 1
                continue
            if in_block_comment:
                buf.append(ch)
                if ch == "*" and nxt == "/":
                    buf.append(nxt)
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if not in_sq and not in_dq:
                if ch == "-" and nxt == "-":
                    in_line_comment = True
                    buf.append(ch)
                    i += 1
                    continue
                if ch == "/" and nxt == "*":
                    in_block_comment = True
                    buf.append(ch)
                    i += 1
                    continue

            # SQL escape '...''...'
            if in_sq and ch == "'" and nxt == "'":
                buf.append(ch)
                buf.append(nxt)
                i += 2
                continue
            if not in_dq and ch == "'":
                in_sq = not in_sq
                buf.append(ch)
                i += 1
                continue
            if not in_sq and ch == '"':
                in_dq = not in_dq
                buf.append(ch)
                i += 1
                continue

            if not in_sq and not in_dq:
                if ch == ";":
                    flush()
                    i += 1
                    continue
                # Oracle SQL*Plus: 자체 라인 단독 / (CRLF/LF 모두)
                if ch == "/" and (i == 0 or ddl[i - 1] in "\n\r"):
                    j = i + 1
                    while j < n and ddl[j] in " \t":
                        j += 1
                    if j == n or ddl[j] in "\n\r":
                        flush()
                        if j + 1 < n and ddl[j] == "\r" and ddl[j + 1] == "\n":
                            i = j + 2
                        elif j < n:
                            i = j + 1
                        else:
                            i = j
                        continue

            buf.append(ch)
            i += 1

        flush()
        return out

    # ════ Stage 2: chunk 정규화 ═════════════════════════════════

    @classmethod
    def _normalize_chunk(cls, chunk: str) -> str:
        """sqlglot이 파싱 가능한 형태로 변환.

          1) 첫 SQL 키워드를 가진 라인부터 시작 (라인/블록 주석 안 키워드는 무시)
          2) Oracle 저장 옵션 절 제거
          3) Oracle BYTE/CHAR 길이 단위 표준화
        """
        out = cls._trim_to_sql_start(chunk)
        out = cls._STORAGE_OPTIONS_RE.sub(r"\1", out)
        out = cls._BYTE_CHAR_LEN_RE.sub(r"(\1)", out)
        return out

    @classmethod
    def _trim_to_sql_start(cls, chunk: str) -> str:
        """라인/블록 주석을 제외한 코드 영역에서 첫 SQL 키워드 라인부터 반환."""
        # 블록 주석을 동일 길이 공백으로 치환 (라인 인덱스 보존)
        cleaned = cls._BLOCK_COMMENT_RE.sub(
            lambda m: " " * (m.end() - m.start()), chunk
        )
        lines_orig = chunk.split("\n")
        lines_clean = cleaned.split("\n")
        for i, line in enumerate(lines_clean):
            idx = line.find("--")
            code = line[:idx] if idx != -1 else line
            if cls._SQL_START_RE.search(code):
                return "\n".join(lines_orig[i:])
        return chunk

    # ════ Stage 3: 디스패처 ═════════════════════════════════════

    def _dispatch_statement(self, stmt: exp.Expression, tables: Dict[str, Dict], dialect: str) -> None:
        """sqlglot expression → 핸들러 라우팅. (COMMENT 는 _apply_comments 에서 처리)"""
        if isinstance(stmt, exp.Create) and (stmt.kind or "").upper() == "TABLE":
            self._handle_create_table(stmt, tables, dialect)
        elif isinstance(stmt, exp.Alter) and (stmt.kind or "").upper() == "TABLE":
            self._handle_alter_table(stmt, tables)

    @staticmethod
    def _parse_chunk(chunk: str, dialect: str):
        """단일 chunk → sqlglot expression(s). 실패 시 silent skip."""
        try:
            for s in sqlglot.parse(chunk, dialect=dialect, error_level=sqlglot.ErrorLevel.IGNORE):
                if s is not None:
                    yield s
        except Exception as e:
            logger.debug(f"[DDL_PARSER] statement skip: {e}")

    @classmethod
    def _detect_dialect(cls, ddl: str) -> str:
        """첫 매칭 마커로 자동 감지. 미감지 시 default."""
        for name, pattern in cls._DIALECT_MARKERS:
            if pattern.search(ddl):
                return name
        return cls.DEFAULT_DIALECT

    # ════ Stage 4: AST → dict 변환 (CREATE TABLE) ═══════════════

    def _handle_create_table(self, stmt: exp.Create, tables: Dict[str, Dict], dialect: str) -> None:
        schema_node = stmt.this  # exp.Schema(table, [ColumnDef|Constraint, ...]) 또는 exp.Table
        if isinstance(schema_node, exp.Schema):
            table_node = schema_node.this
            defs = schema_node.expressions or []
        elif isinstance(schema_node, exp.Table):
            table_node, defs = schema_node, []
        else:
            return
        if not isinstance(table_node, exp.Table):
            return

        schema, name = self._qualified(table_node)
        if not name:
            return

        key = self._key(schema, name)
        columns: List[Dict[str, Any]] = []
        primary_keys: List[str] = []
        foreign_keys: List[Dict[str, str]] = []

        for d in defs:
            if isinstance(d, exp.ColumnDef):
                col, is_pk, fk = self._extract_column(d, dialect)
                columns.append(col)
                if is_pk:
                    primary_keys.append(col["name"])
                if fk:
                    foreign_keys.append(fk)
            elif isinstance(d, exp.PrimaryKey):
                primary_keys.extend(self._extract_pk_columns(d))
            elif isinstance(d, exp.ForeignKey):
                foreign_keys.extend(self._extract_fk_records(d))
            elif isinstance(d, exp.Constraint):
                # CONSTRAINT name PRIMARY KEY/FOREIGN KEY (...)
                for inner in (d.expressions or []):
                    if isinstance(inner, exp.PrimaryKey):
                        primary_keys.extend(self._extract_pk_columns(inner))
                    elif isinstance(inner, exp.ForeignKey):
                        foreign_keys.extend(self._extract_fk_records(inner))

        tables[key] = {
            "table": {
                "schema": schema,
                "name": name,
                "comment": self._extract_inline_table_comment(stmt),
                "table_type": "BASE TABLE",
            },
            "columns": columns,
            "foreignKeys": foreign_keys,
            "primaryKeys": primary_keys,
        }

    def _handle_alter_table(self, stmt: exp.Alter, tables: Dict[str, Dict]) -> None:
        target = stmt.this
        if not isinstance(target, exp.Table):
            return
        key = self._key(*self._qualified(target))
        if key not in tables:
            return
        for action in (stmt.args.get("actions") or []):
            self._apply_alter_action(action, tables[key])

    def _apply_alter_action(self, action: exp.Expression, table_dict: Dict[str, Any]) -> None:
        if isinstance(action, exp.PrimaryKey):
            table_dict["primaryKeys"] = self._extract_pk_columns(action)
        elif isinstance(action, exp.ForeignKey):
            table_dict["foreignKeys"].extend(self._extract_fk_records(action))
        elif isinstance(action, (exp.AddConstraint, exp.Constraint)):
            for inner in (action.expressions or []):
                self._apply_alter_action(inner, table_dict)

    def _extract_column(
        self, col_def: exp.ColumnDef, dialect: str
    ) -> Tuple[Dict[str, Any], bool, Optional[Dict[str, str]]]:
        col_name = col_def.name
        dtype = self._format_dtype(col_def.args.get("kind"), dialect)

        nullable = True
        is_primary = False
        is_unique = False
        default_value: Optional[str] = None
        comment_text = ""
        fk: Optional[Dict[str, str]] = None

        for cons in (col_def.args.get("constraints") or []):
            inner = cons.args.get("kind") if isinstance(cons, exp.ColumnConstraint) else cons
            if isinstance(inner, exp.NotNullColumnConstraint):
                nullable = False
            elif isinstance(inner, exp.PrimaryKeyColumnConstraint):
                is_primary = True
                nullable = False
            elif isinstance(inner, exp.UniqueColumnConstraint):
                is_unique = True
            elif isinstance(inner, exp.DefaultColumnConstraint):
                default_value = self._format_default(inner.this)
            elif isinstance(inner, exp.CommentColumnConstraint):
                tgt = inner.this
                comment_text = tgt.name if isinstance(tgt, exp.Literal) else (tgt.sql() if tgt else "")
            elif isinstance(inner, exp.Reference):
                fk = self._extract_inline_fk(col_name, inner)

        col: Dict[str, Any] = {
            "name": col_name,
            "dtype": dtype,
            "nullable": nullable,
            "comment": comment_text,
            "primary_key": is_primary,
        }
        if is_unique:
            col["unique"] = True
        if default_value is not None:
            col["default_value"] = default_value
        return col, is_primary, fk

    @staticmethod
    def _extract_pk_columns(pk: exp.PrimaryKey) -> List[str]:
        return [DDLParser._identifier_name(e) for e in (pk.expressions or []) if e]

    @classmethod
    def _extract_fk_records(cls, fk: exp.ForeignKey) -> List[Dict[str, str]]:
        ref = fk.args.get("reference")
        if not isinstance(ref, exp.Reference):
            return []
        src_cols = [cls._identifier_name(e) for e in (fk.expressions or [])]
        return cls._build_fk_records(src_cols, ref)

    @classmethod
    def _extract_inline_fk(cls, col_name: str, ref: exp.Reference) -> Optional[Dict[str, str]]:
        records = cls._build_fk_records([col_name], ref)
        return records[0] if records else None

    @classmethod
    def _build_fk_records(cls, src_cols: List[str], ref: exp.Reference) -> List[Dict[str, str]]:
        ref_target = ref.this
        if isinstance(ref_target, exp.Schema):
            ref_table = ref_target.this
            ref_cols = [cls._identifier_name(e) for e in (ref_target.expressions or [])]
        elif isinstance(ref_target, exp.Table):
            ref_table, ref_cols = ref_target, []
        else:
            return []
        if not isinstance(ref_table, exp.Table):
            return []

        ref_schema, ref_name = cls._qualified(ref_table)
        prefix = f"{ref_schema}.{ref_name}" if ref_schema else ref_name
        if not ref_cols:
            ref_cols = [""] * len(src_cols)
        return [{"column": s, "ref": f"{prefix}.{r}"} for s, r in zip(src_cols, ref_cols) if s]

    @staticmethod
    def _extract_inline_table_comment(stmt: exp.Create) -> str:
        """CREATE TABLE ... COMMENT='...' (MySQL/PG 인라인). Oracle은 Stage 5에서 처리."""
        props = stmt.args.get("properties")
        if not isinstance(props, exp.Properties):
            return ""
        for p in (props.expressions or []):
            if isinstance(p, exp.SchemaCommentProperty):
                v = p.this
                if isinstance(v, exp.Literal):
                    return v.name
                return v.sql() if v else ""
        return ""

    # ════ Stage 5: COMMENT ON 적용 (regex hybrid) ════════════════

    @classmethod
    def _apply_comments(cls, ddl: str, tables: Dict[str, Dict]) -> None:
        """원문 전체에서 COMMENT ON TABLE/COLUMN을 직접 매칭해 적용.

        sqlglot의 COMMENT 파싱은 한글·괄호·콤마 등 특수문자가 포함된 IS '...'
        리터럴에서 종종 깨지므로, 형식이 표준화된 COMMENT ON 만은 정규식으로
        결정론 매칭하는 것이 더 robust하고 단순함.
        """
        for m in cls._COMMENT_ON_TABLE_RE.finditer(ddl):
            schema = m.group("schema") or ""
            name = m.group("name")
            body = m.group("body").replace("''", "'")
            key = cls._key(schema, name)
            if key in tables:
                tables[key]["table"]["comment"] = body

        for m in cls._COMMENT_ON_COLUMN_RE.finditer(ddl):
            schema = m.group("schema") or ""
            table = m.group("table")
            col = m.group("col")
            body = m.group("body").replace("''", "'")
            key = cls._key(schema, table)
            if key not in tables:
                continue
            for c in tables[key]["columns"]:
                if c["name"].lower() == col.lower():
                    c["comment"] = body
                    break

    # ════ 유틸 ═══════════════════════════════════════════════════

    @staticmethod
    def _qualified(t: exp.Table) -> Tuple[str, str]:
        """Table 식 → (schema, name). catalog 무시."""
        return (t.db or "", t.name or "")

    @staticmethod
    def _key(schema: str, name: str) -> str:
        """(schema, name) → 정규화된 lookup 키 (소문자)."""
        return f"{(schema or '').lower()}.{(name or '').lower()}"

    @staticmethod
    def _identifier_name(node: exp.Expression) -> str:
        if hasattr(node, "name") and node.name:
            return node.name
        return node.sql().strip('"').strip("'") if node else ""

    @staticmethod
    def _format_dtype(kind: Optional[exp.DataType], dialect: str) -> str:
        if kind is None:
            return ""
        try:
            return kind.sql(dialect=dialect).upper().strip()
        except Exception:  # noqa: SILENT_FAIL — sqlglot dialect 미지원 fallback (재호출, debug 만 충분)
            return kind.sql().upper().strip()

    @staticmethod
    def _format_default(value: Optional[exp.Expression]) -> str:
        if value is None:
            return ""
        if isinstance(value, exp.Literal):
            return f"'{value.name}'" if value.is_string else str(value.name)
        try:
            return value.sql()
        except Exception:  # noqa: SILENT_FAIL — sqlglot expression.sql() fallback (str 변환, debug 만 충분)
            return str(value)


# ── 모듈 레벨 편의 API (호환성 유지) ───────────────────────────

_parser_instance: Optional[DDLParser] = None


def get_ddl_parser() -> DDLParser:
    """싱글톤 인스턴스."""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = DDLParser()
    return _parser_instance


def parse_ddl(ddl_content: str, dialect: Optional[str] = None) -> Dict[str, Any]:
    """편의 함수. dialect 미지정 시 자동 감지."""
    return get_ddl_parser().parse(ddl_content, dialect=dialect)


# ═══════════════════════════════════════════════════════════════════════
# Section 3: DDL 처리 + Neo4j 반영
# ═══════════════════════════════════════════════════════════════════════


async def process_ddl(
    ddl_path: str,
    client: Neo4jClient,
    file_name: str,
    orchestrator: Any,
    ddl_schemas: Set[str],
    ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]],
    emit_progress: bool = True,
    file_base_progress: int = 0,
    file_end_progress: int = 100,
    datasource_name: str = "",
) -> AsyncGenerator[bytes | Tuple[dict, dict], None]:
    """DDL 파일 처리 및 테이블/컬럼 노드 생성 (스트리밍)"""
    ddl_stats = {"tables": 0, "columns": 0, "fks": 0}
    file_range = file_end_progress - file_base_progress
    parsing_end = file_base_progress + int(file_range * 0.5)
    saving_start = parsing_end
    saving_end = file_end_progress

    ddl_content, encoding_used = await read_text_with_fallback(
        ddl_path,
        preferred_encodings=("utf-8", "utf-8-sig", "cp949", "euc-kr"),
    )
    if encoding_used != "utf-8":
        log_process("ANALYZE", "DDL", f"DDL 인코딩 fallback 적용: {file_name} ({encoding_used})")

    total_tokens = calculate_code_token(ddl_content)

    if emit_progress:
        yield emit_user_message(f"   ⚡ sqlglot AST 파서 사용 (다중 방언 자동 감지)")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=file_base_progress + int(file_range * 0.1),
            details={"mode": "sqlglot", "tokens": total_tokens}
        )

    try:
        parsed = await asyncio.to_thread(parse_ddl, ddl_content)
        all_parsed_results = parsed.get("analysis", [])
        table_count = len(all_parsed_results)
        if emit_progress:
            table_names = [t.get("table", {}).get("name", "?") for t in all_parsed_results[:5]]
            preview = ", ".join(table_names)
            if table_count > 5:
                preview += f" 외 {table_count - 5}개"
            yield emit_user_message(f"   ✅ 파싱 완료: {table_count}개 테이블 ({preview})")
            yield emit_phase_event(
                phase_num=0,
                phase_name="DDL 처리",
                status="in_progress",
                progress=parsing_end,
                details={"tables_parsed": table_count, "mode": "sqlglot"}
            )
    except Exception as e:
        if emit_progress:
            yield emit_user_message(f"   ❌ DDL 파싱 실패: {str(e)[:80]}")
        raise RuntimeError(f"DDL 파싱 실패: {e}")

    parsed = {"analysis": all_parsed_results}
    # target이 프로그래밍 언어(c, java 등)일 때는 DB 기본값(postgres) 사용
    # orchestrator.target 은 service 진입점에서 검증된 비-빈 값. 비어있으면 fail-fast.
    if not orchestrator.target:
        raise ValueError(
            "orchestrator.target 필수 — service 진입점(AnalysisContext)이 비-빈 값을 보장해야 함"
        )
    _LANG_TARGETS = {"c", "java", "python", "go", "kotlin", "scala", "rust"}
    _raw_target = orchestrator.target.lower()
    # 코드 언어 (C/Java 등) 는 DDL dialect 정보가 없으므로 settings.defaults.db_dialect 우선,
    # 없으면 빈 문자열 (unknown). postgres 같은 임의 값 박지 않음 (Oracle/DB2 등에서 mismatch 방지).
    db_name = (
        settings.defaults.db_dialect if _raw_target in _LANG_TARGETS and settings.defaults.db_dialect
        else (_raw_target if _raw_target not in _LANG_TARGETS else "")
    )
    name_case = orchestrator.name_case

    schemas_data = []
    tables_data = []
    columns_data = []
    fks_data = []
    seen_schemas = set()
    seen_tables = set()

    for table_info in parsed.get("analysis", []):
        table = table_info.get("table", {})
        columns = table_info.get("columns", [])
        foreign_keys = table_info.get("foreignKeys", [])
        primary_keys = [
            str(pk).strip().upper()
            for pk in (table_info.get("primaryKeys") or [])
            if pk
        ]

        schema_raw = (table.get("schema") or "").strip()
        table_name_raw = (table.get("name") or "").strip()
        comment = (table.get("comment") or "").strip()
        table_type = (table.get("table_type") or "BASE TABLE").strip().upper()
        qualified = f"{schema_raw}.{table_name_raw}" if schema_raw else table_name_raw
        parsed_schema, parsed_name, _ = parse_table_identifier(qualified)
        # parsed_schema 없으면 빈값 그대로 둠 — 강제 'public' 박지 X (정확도 우선).
        # 이후 코드가 LLM/own_schema/DDL lookup 위계 (resolve_schema) 로 채움.
        schema = apply_name_case(parsed_schema, name_case) if parsed_schema else ""
        parsed_name = apply_name_case(parsed_name, name_case)

        if schema:
            ddl_schemas.add(schema)

        schema_key = (db_name, schema)
        if schema_key not in seen_schemas:
            seen_schemas.add(schema_key)
            schemas_data.append({
                "db": db_name,
                "name": schema,
                "datasource": datasource_name
            })

        table_key = (db_name, schema, parsed_name)
        if table_key not in seen_tables:
            seen_tables.add(table_key)
            tables_data.append({
                "db": db_name,
                "schema": schema,
                "name": parsed_name,
                "description": comment,
                "description_source": "ddl" if comment else "",
                "table_type": table_type,
                "datasource": datasource_name
            })
            ddl_stats["tables"] += 1

        column_metadata = {}
        for col in columns:
            col_name_raw = (col.get("name") or "").strip()
            if not col_name_raw:
                continue
            col_name = apply_name_case(col_name_raw, name_case)
            col_comment = (col.get("comment") or "").strip()
            col_meta = {
                "description": col_comment,
                "dtype": (col.get("dtype") or col.get("type") or "").strip(),
                "nullable": col.get("nullable", True),
            }
            if col.get("unique"):
                col_meta["unique"] = True
            if col.get("default_value") is not None:
                col_meta["default_value"] = col["default_value"]
            column_metadata[col_name] = col_meta
        cache_key = (schema.lower(), parsed_name.lower())
        ddl_table_metadata[cache_key] = {
            "description": comment,
            "columns": column_metadata,
            "original_schema": schema,
            "original_name": parsed_name,
        }

        for col in columns:
            col_name_raw = (col.get("name") or "").strip()
            if not col_name_raw:
                continue
            col_name = apply_name_case(col_name_raw, name_case)
            col_type = (col.get("dtype") or col.get("type") or "").strip()
            col_nullable = col.get("nullable", True)
            col_comment = (col.get("comment") or "").strip()
            fqn = ".".join(filter(None, [datasource_name, schema, parsed_name, col_name])).lower()
            is_primary_key = col.get("primary_key", False) or (col_name_raw.upper() in primary_keys)
            is_unique = col.get("unique", False)
            default_value = col.get("default_value")
            col_data = {
                "fqn": fqn,
                "name": col_name,
                "dtype": col_type,
                "description": col_comment,
                "description_source": "ddl" if col_comment else "",
                "nullable": col_nullable,
                "is_primary_key": is_primary_key,
                "is_unique": is_unique,
                "table_db": db_name,
                "table_schema": schema,
                "table_name": parsed_name,
                "datasource": datasource_name
            }
            if is_primary_key:
                col_data["pk_constraint"] = f"{parsed_name}_pkey"
            if default_value is not None:
                col_data["default_value"] = default_value
            columns_data.append(col_data)
            ddl_stats["columns"] += 1

        for fk in foreign_keys:
            src_col_raw = (fk.get("column") or "").strip()
            ref = (fk.get("ref") or "").strip()
            if not src_col_raw or not ref or "." not in ref:
                continue
            ref_table_part, ref_col_raw = ref.rsplit(".", 1)
            ref_schema_parsed, ref_table_raw, _ = parse_table_identifier(ref_table_part)
            ref_schema_final = apply_name_case(ref_schema_parsed or schema, name_case)
            ref_table = apply_name_case(ref_table_raw, name_case)
            src_col = apply_name_case(src_col_raw, name_case)
            ref_col = apply_name_case(ref_col_raw, name_case)
            fks_data.append({
                "from_db": db_name,
                "from_schema": schema,
                "from_table": parsed_name,
                "from_column": src_col,
                "to_db": db_name,
                "to_schema": ref_schema_final or "",
                "to_table": ref_table or "",
                "to_column": ref_col
            })
            ddl_stats["fks"] += 1

    if emit_progress:
        yield emit_user_message(f"   💾 UNWIND 배치 저장 시작: {ddl_stats['tables']}개 테이블, {ddl_stats['columns']}개 컬럼, {ddl_stats['fks']}개 FK")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_start,
            details={"step": "unwind_batch", "tables": ddl_stats['tables'], "columns": ddl_stats['columns'], "fks": ddl_stats['fks']}
        )

    all_nodes: dict = {}
    all_relationships: dict = {}

    if schemas_data:
        if emit_progress:
            yield emit_user_message(f"      📦 [1/7] 스키마 {len(schemas_data)}개 생성 중...")
        result = await client.run_batch_unwind(SCHEMA_MERGE_QUERY, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if schemas_data and datasource_name:
        if emit_progress:
            yield emit_user_message(f"      🔗 DataSource({datasource_name}) 노드 생성 및 스키마 연결 중...")
        await client.execute_queries([DATASOURCE_CREATE_QUERY], {"datasource_name": datasource_name})
        result = await client.run_batch_unwind(DATASOURCE_SCHEMA_QUERY, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if tables_data:
        if emit_progress:
            yield emit_user_message(f"      📦 [2/7] 테이블 {len(tables_data)}개 생성 중...")
        result = await client.run_batch_unwind(TABLE_MERGE_QUERY, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if tables_data:
        if emit_progress:
            yield emit_user_message(f"      📦 [3/7] 테이블-스키마 관계 {len(tables_data)}개 생성 중...")
        result = await client.run_batch_unwind(BELONGS_TO_QUERY, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if columns_data:
        if emit_progress:
            yield emit_user_message(f"      📦 [4/7] 컬럼 {len(columns_data)}개 생성 중...")
        result = await client.run_batch_unwind(COLUMN_MERGE_QUERY, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if columns_data:
        if emit_progress:
            yield emit_user_message(f"      📦 [5/7] 테이블-컬럼 관계 {len(columns_data)}개 생성 중...")
        result = await client.run_batch_unwind(HAS_COLUMN_QUERY, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if fks_data:
        if emit_progress:
            yield emit_user_message(f"      📦 [6/7] FK 관계 {len(fks_data)}개 생성 중...")
        result = await client.run_batch_unwind(FK_REF_TABLE_QUERY, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        result = await client.run_batch_unwind(FK_TO_TABLE_QUERY, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

        result = await client.run_batch_unwind(FK_TO_COLUMN_QUERY, fks_data)
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if emit_progress:
        yield emit_user_message(f"   ✅ UNWIND 배치 저장 완료: {len(all_nodes)}개 노드, {len(all_relationships)}개 관계")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_end,
            details={"step": "unwind_completed", "nodes_created": len(all_nodes), "relationships_created": len(all_relationships)}
        )

    graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
    if emit_progress:
        yield emit_user_message(f"   ✅ Neo4j 저장 완료: {len(graph['Nodes'])}개 노드, {len(graph['Relationships'])}개 관계 생성")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_end,
            details={"step": "neo4j_saved", "tables": ddl_stats['tables'], "columns": ddl_stats['columns'], "fks": ddl_stats['fks'], "nodes_created": len(graph['Nodes']), "relationships_created": len(graph['Relationships'])}
        )

    # 그래프 데이터를 bytes로 직접 스트리밍 (프론트에서 즉시 렌더링)
    yield emit_data(graph=graph)

    log_process("ANALYZE", "DDL", f"DDL 처리 완료: {file_name} (T:{ddl_stats['tables']}, C:{ddl_stats['columns']}, FK:{ddl_stats['fks']})")


# ═══════════════════════════════════════════════════════════════════════
# Section 4: DDL 처리 루프
# ═══════════════════════════════════════════════════════════════════════


async def run_ddl_processing(
    *,
    orchestrator: Any,
    client: Any,
    ddl_schemas: set,
    ddl_table_metadata: dict,
    datasource_name: str = "",
    skip_message: str = "📋 DDL 파일 없음 — DDL 처리 생략",
    on_file_start: Optional[Any] = None,
) -> AsyncGenerator[bytes, None]:
    """DDL 파일 처리 공통 루프.

    동시 쓰기 충돌은 ``client.run_batch_unwind`` 의 managed transaction 이 자동 재시도 처리.

    Args:
        orchestrator: 오케스트레이터 (dirs, datasource_name 등)
        client: Neo4j 클라이언트
        ddl_schemas: DDL에서 발견된 스키마 이름 set (in-place 수정)
        ddl_table_metadata: DDL 테이블 메타데이터 dict (in-place 수정)
        datasource_name: 데이터소스 이름
        skip_message: DDL 없을 때 메시지
        on_file_start: 파일 시작 시 콜백 (idx, total, fname) -> bytes

    Yields:
        NDJSON bytes
    """
    ddl_files = list_ddl_files(orchestrator)
    if not ddl_files:
        yield emit_user_message(skip_message)
        return

    ddl_dir = orchestrator.dirs.get("ddl", "")
    total_files = len(ddl_files)
    ds_name = datasource_name or orchestrator.datasource_name or ""

    yield emit_user_message(f"📋 DDL 파일 {total_files}개 처리 시작")

    for idx, fname in enumerate(ddl_files):
        ddl_path = os.path.join(ddl_dir, fname)
        file_base = int((idx / total_files) * 100)
        file_end = int(((idx + 1) / total_files) * 100)

        if on_file_start:
            yield on_file_start(idx + 1, total_files, fname)
        else:
            yield emit_user_message(f"  📄 [{idx + 1}/{total_files}] {fname}")

        try:
            async for chunk in process_ddl(
                ddl_path=ddl_path,
                client=client,
                file_name=fname,
                orchestrator=orchestrator,
                ddl_schemas=ddl_schemas,
                ddl_table_metadata=ddl_table_metadata,
                emit_progress=True,
                file_base_progress=file_base,
                file_end_progress=file_end,
                datasource_name=ds_name,
            ):
                if isinstance(chunk, bytes):
                    yield chunk
        except Exception as e:  # noqa: SILENT_FAIL — 한 DDL 파일 실패가 다른 파일 처리를 막지 않도록 errlog 후 다음 파일 진행
            logger.error("[PHASE:0:DDL] DDL 처리 실패 | file=%s error=%s", fname, e, exc_info=True)
            yield emit_user_message(f"  ⚠️ DDL 처리 실패: {fname} ({str(e)[:60]})")

        yield emit_phase_event(0, "DDL 처리", "running", file_end)

    yield emit_user_message(f"✅ DDL 처리 완료 | schemas={len(ddl_schemas)} tables={len(ddl_table_metadata)}")


__all__ = [
    "apply_name_case",
    "list_ddl_files",
    "DDLParser",
    "get_ddl_parser",
    "parse_ddl",
    "process_ddl",
    "run_ddl_processing",
]
