"""DDL 처리 — SQL 파싱 + 파일 처리 + Neo4j 스키마/테이블 반영

analyzer/common/ 공유 모듈 — Framework, DBMS 양쪽 strategy에서 사용.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple

from client.neo4j_client import Neo4jClient
from util.encoding_reader import read_text_with_fallback
from util.logger import log_process
from util.stream_emitter import (
    emit_message,
    emit_phase_event,
)
from util.code_text_utils import (
    parse_table_identifier,
    calculate_code_token,
)
from analyzer.common.graph_writer import (
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


# =====================================================================
# Section 1: 유틸리티 (from ddl_utils.py)
# =====================================================================


def apply_name_case(name: str, name_case: str) -> str:
    """메타데이터 대소문자 변환 적용"""
    if not name:
        return name
    if name_case == "uppercase":
        return name.upper()
    elif name_case == "lowercase":
        return name.lower()
    return name


def list_ddl_files(orchestrator: Any) -> List[str]:
    """DDL 파일 목록 조회

    DDL 디렉토리가 없거나 파일이 없으면 빈 리스트 반환 (경고 처리, 에러 아님)
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
    except OSError as e:
        log_process("ANALYZE", "DDL", f"DDL 디렉토리 읽기 실패: {ddl_dir} - {e}")
        return []


# =====================================================================
# Section 2: DDL SQL 파서 (from ddl_parser.py)
# =====================================================================


class DDLParser:
    """정규식 기반 DDL 파서

    Usage:
        parser = DDLParser()
        result = parser.parse(ddl_content)
        # result = {"analysis": [{"table": {...}, "columns": [...], "foreignKeys": [...], "primaryKeys": [...]}, ...]}
    """

    def __init__(self):
        # CREATE TABLE 패턴 (여러 줄에 걸친 정의 지원)
        self.create_table_pattern = re.compile(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
            r'([\w\."]+)\s*\('
            r'(.*?)'
            r'\)\s*;',
            re.IGNORECASE | re.DOTALL
        )

        # 테이블 코멘트 패턴 (이스케이프된 따옴표 처리)
        self.table_comment_pattern = re.compile(
            r"COMMENT\s+ON\s+TABLE\s+([\w\.\"]+)\s+IS\s+'((?:[^']|'')*?)'\s*;",
            re.IGNORECASE | re.DOTALL
        )

        # 컬럼 코멘트 패턴
        self.column_comment_pattern = re.compile(
            r"COMMENT\s+ON\s+COLUMN\s+([\w\.\"]+)\.([\w\"]+)\s+IS\s+'((?:[^']|'')*?)'\s*;",
            re.IGNORECASE | re.DOTALL
        )

        # PRIMARY KEY 패턴
        self.pk_pattern = re.compile(
            r'ALTER\s+TABLE\s+([\w\.\"]+)\s+ADD\s+(?:CONSTRAINT\s+[\w\"]+\s+)?PRIMARY\s+KEY\s*\(([^)]+)\)\s*;',
            re.IGNORECASE
        )

        # FOREIGN KEY 패턴 (ALTER TABLE)
        self.fk_pattern = re.compile(
            r'ALTER\s+TABLE\s+([\w\.\"]+)\s+ADD\s+(?:CONSTRAINT\s+[\w\"]+\s+)?'
            r'FOREIGN\s+KEY\s*\(([^)]+)\)\s+'
            r'REFERENCES\s+([\w\.\"]+)\s*\(([^)]+)\)',
            re.IGNORECASE
        )

        # ALTER TABLE 블록 패턴 (DROP/ADD 액션 혼합 지원)
        self.alter_table_pattern = re.compile(
            r'ALTER\s+TABLE\s+([\w\.\"]+)\s+(.*?);',
            re.IGNORECASE | re.DOTALL
        )

        # ALTER TABLE 액션 내 ADD PRIMARY KEY / ADD FOREIGN KEY 패턴
        self.alter_action_pk_pattern = re.compile(
            r'ADD\s+(?:CONSTRAINT\s+[\w\"]+\s+)?PRIMARY\s+KEY\s*\(([^)]+)\)',
            re.IGNORECASE
        )
        self.alter_action_fk_pattern = re.compile(
            r'ADD\s+(?:CONSTRAINT\s+[\w\"]+\s+)?FOREIGN\s+KEY\s*\(([^)]+)\)\s+'
            r'REFERENCES\s+([\w\.\"]+)\s*\(([^)]+)\)',
            re.IGNORECASE
        )

        # 인라인 FOREIGN KEY 패턴 (CREATE TABLE 내부)
        # FOREIGN KEY (column) REFERENCES schema.table(column)
        self.inline_fk_pattern = re.compile(
            r'FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+([\w\.\"]+)\s*\(([^)]+)\)',
            re.IGNORECASE
        )

        # 컬럼 수준 인라인 REFERENCES 패턴 (CREATE TABLE 내부)
        # 예: PLAN_ID NUMBER(10) REFERENCES PLAN(PLAN_ID)
        # 예: SUBSCRIBER_ID NUMBER(15) NOT NULL REFERENCES SUBSCRIBER(SUBSCRIBER_ID)
        # 한 줄 내에서만 매칭 (줄바꿈 제외: [^\n] 사용)
        self.column_ref_pattern = re.compile(
            r'(\w+)\s+[^\n]+?REFERENCES\s+([\w\.\"]+)\s*\(([^)]+)\)',
            re.IGNORECASE
        )

        # 인라인 PRIMARY KEY 패턴 (CREATE TABLE 내부)
        # PRIMARY KEY (col1, col2)
        self.inline_pk_pattern = re.compile(
            r'PRIMARY\s+KEY\s*\(([^)]+)\)',
            re.IGNORECASE
        )

        # 컬럼 정의 패턴 (CREATE TABLE 내부)
        self.column_def_pattern = re.compile(
            r'^\s*"?(\w+)"?\s+'  # 컬럼명
            r'([A-Za-z_]+(?:\s*\([^)]*\))?)'  # 데이터 타입
            r'(\s+NOT\s+NULL)?'  # NOT NULL
            r'(\s+DEFAULT\s+[^,]+)?'  # DEFAULT
            r'\s*,?\s*$',
            re.IGNORECASE | re.MULTILINE
        )

    def parse(self, ddl_content: str) -> Dict[str, Any]:
        """DDL 전체 파싱

        Args:
            ddl_content: DDL 파일 내용

        Returns:
            LLM 파서와 동일한 형식의 결과
            {"analysis": [{"table": {...}, "columns": [...], "foreignKeys": [...], "primaryKeys": [...]}, ...]}
        """
        tables: Dict[str, Dict] = {}  # table_key -> table_info

        # 1. CREATE TABLE 파싱
        for match in self.create_table_pattern.finditer(ddl_content):
            full_name = match.group(1)
            columns_def = match.group(2)
            create_table_start = match.start()

            schema, table_name = self._parse_table_name(full_name)
            table_key = f"{schema}.{table_name}".lower()

            columns = self._parse_columns(columns_def)

            # CREATE TABLE 위의 주석 추출
            table_comment = self._extract_table_comment(ddl_content, create_table_start)

            # 인라인 FK 파싱 (CREATE TABLE 내부)
            inline_fks = []
            for fk_match in self.inline_fk_pattern.finditer(columns_def):
                src_columns = [self._strip_quotes(c.strip()) for c in fk_match.group(1).split(",")]
                ref_table = fk_match.group(2)
                ref_columns = [self._strip_quotes(c.strip()) for c in fk_match.group(3).split(",")]

                ref_schema, ref_name = self._parse_table_name(ref_table)

                for src_col, ref_col in zip(src_columns, ref_columns):
                    inline_fks.append({
                        "column": src_col,
                        "ref": f"{ref_schema}.{ref_name}.{ref_col}" if ref_schema else f"{ref_name}.{ref_col}",
                    })

            # 컬럼 수준 인라인 REFERENCES 파싱 (CREATE TABLE 내부)
            # 예: PLAN_ID NUMBER(10) REFERENCES PLAN(PLAN_ID)
            existing_fk_cols = {fk["column"] for fk in inline_fks}
            for col_ref_match in self.column_ref_pattern.finditer(columns_def):
                src_col = self._strip_quotes(col_ref_match.group(1))
                if src_col in existing_fk_cols:
                    continue  # 이미 FOREIGN KEY 절로 추출된 경우 중복 방지
                ref_table = col_ref_match.group(2)
                ref_col = self._strip_quotes(col_ref_match.group(3).strip())
                ref_schema, ref_name = self._parse_table_name(ref_table)
                inline_fks.append({
                    "column": src_col,
                    "ref": f"{ref_schema}.{ref_name}.{ref_col}" if ref_schema else f"{ref_name}.{ref_col}",
                })

            # 인라인 PK 파싱 (CREATE TABLE 내부)
            inline_pks = []
            for pk_match in self.inline_pk_pattern.finditer(columns_def):
                pk_columns = [self._strip_quotes(c.strip()) for c in pk_match.group(1).split(",")]
                inline_pks.extend(pk_columns)

            tables[table_key] = {
                "table": {
                    "schema": schema,
                    "name": table_name,
                    "comment": table_comment,
                    "table_type": "BASE TABLE",
                },
                "columns": columns,
                "foreignKeys": inline_fks,
                "primaryKeys": inline_pks,
            }

            logger.debug(f"[DDL_PARSER] 테이블 {table_key}: 컬럼 {len(columns)}개, 인라인 FK {len(inline_fks)}개, 인라인 PK {len(inline_pks)}개")

        # 2. 테이블 코멘트 파싱
        for match in self.table_comment_pattern.finditer(ddl_content):
            full_name = match.group(1)
            comment = match.group(2).replace("''", "'")  # 이스케이프 처리

            schema, table_name = self._parse_table_name(full_name)
            table_key = f"{schema}.{table_name}".lower()

            if table_key in tables:
                tables[table_key]["table"]["comment"] = comment

        # 3. 컬럼 코멘트 파싱
        for match in self.column_comment_pattern.finditer(ddl_content):
            full_table = match.group(1)
            col_name = self._strip_quotes(match.group(2))
            comment = match.group(3).replace("''", "'")

            schema, table_name = self._parse_table_name(full_table)
            table_key = f"{schema}.{table_name}".lower()

            if table_key in tables:
                for col in tables[table_key]["columns"]:
                    if col["name"].lower() == col_name.lower():
                        col["comment"] = comment
                        break

        # 4~5. ALTER TABLE 액션 기반 PRIMARY KEY / FOREIGN KEY 파싱
        # DROP CONSTRAINT, ADD CONSTRAINT ... NOT VALID 같은 혼합 구문을 지원한다.
        for match in self.alter_table_pattern.finditer(ddl_content):
            full_name = match.group(1)
            alter_actions = match.group(2)

            schema, table_name = self._parse_table_name(full_name)
            table_key = f"{schema}.{table_name}".lower()
            if table_key not in tables:
                continue

            for action in self._split_alter_actions(alter_actions):
                pk_match = self.alter_action_pk_pattern.search(action)
                if pk_match:
                    pk_columns = [self._strip_quotes(c.strip()) for c in pk_match.group(1).split(",")]
                    tables[table_key]["primaryKeys"] = pk_columns
                    continue

                fk_match = self.alter_action_fk_pattern.search(action)
                if fk_match:
                    src_columns = [self._strip_quotes(c.strip()) for c in fk_match.group(1).split(",")]
                    ref_table = fk_match.group(2)
                    ref_columns = [self._strip_quotes(c.strip()) for c in fk_match.group(3).split(",")]

                    ref_schema, ref_name = self._parse_table_name(ref_table)
                    for src_col, ref_col in zip(src_columns, ref_columns):
                        tables[table_key]["foreignKeys"].append({
                            "column": src_col,
                            "ref": f"{ref_schema}.{ref_name}.{ref_col}" if ref_schema else f"{ref_name}.{ref_col}",
                        })

        # 결과 변환
        result = {"analysis": list(tables.values())}

        logger.info(f"[DDL_PARSER] 정규식 파싱 완료: {len(tables)}개 테이블")

        return result

    def _parse_table_name(self, full_name: str) -> tuple[str, str]:
        """스키마.테이블명 파싱

        Args:
            full_name: 'SCHEMA."TABLE_NAME"' 또는 '"TABLE_NAME"' 형식

        Returns:
            (schema, table_name) 튜플
        """
        parts = full_name.split(".")
        if len(parts) >= 2:
            schema = self._strip_quotes(parts[0])
            table_name = self._strip_quotes(parts[1])
        else:
            schema = ""
            table_name = self._strip_quotes(parts[0])
        return schema, table_name

    def _strip_quotes(self, s: str) -> str:
        """따옴표 제거"""
        s = s.strip()
        if len(s) >= 2:
            if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
                return s[1:-1]
        return s

    def _extract_table_comment(self, ddl_content: str, create_table_start: int) -> str:
        """CREATE TABLE 위의 주석 추출

        Args:
            ddl_content: DDL 전체 내용
            create_table_start: CREATE TABLE 시작 위치

        Returns:
            추출된 주석 (한 줄이면 그대로, 블록이면 합쳐서)
        """
        if create_table_start == 0:
            return ""

        # CREATE TABLE 이전 텍스트
        before_text = ddl_content[:create_table_start]
        lines = before_text.split('\n')

        # 역순으로 검사하여 연속된 주석 블록 찾기
        comment_lines = []
        for line in reversed(lines):
            stripped = line.strip()
            # 빈 줄이면 중단
            if not stripped:
                if comment_lines:
                    break
                continue
            # -- 주석이면 수집
            if stripped.startswith('--'):
                comment_text = stripped[2:].strip()
                if comment_text:
                    comment_lines.insert(0, comment_text)
            else:
                # 주석이 아닌 줄(SQL 구문)을 만나면 항상 중단
                # comment_lines 유무와 관계없이 중단하여 파일 헤더 주석이
                # 다른 테이블의 설명으로 잘못 포함되는 것을 방지
                break

        if not comment_lines:
            return ""

        # 한 줄이면 그대로, 여러 줄이면 합치기
        return comment_lines[0] if len(comment_lines) == 1 else '\n'.join(comment_lines)

    def _split_alter_actions(self, actions: str) -> List[str]:
        """ALTER TABLE 액션 문자열을 최상위 콤마 기준으로 분리

        괄호 내부의 콤마는 무시하고, 인용부호(따옴표) 이스케이프도 처리합니다.

        예:
            DROP CONSTRAINT a, ADD CONSTRAINT b FOREIGN KEY (x) REFERENCES t(y), DROP CONSTRAINT c
        """
        result: List[str] = []
        current: List[str] = []
        paren_depth = 0
        in_single_quote = False
        in_double_quote = False

        i = 0
        while i < len(actions):
            ch = actions[i]

            # 작은따옴표 문자열 처리 ('' 이스케이프 지원)
            if not in_double_quote and ch == "'":
                if in_single_quote and i + 1 < len(actions) and actions[i + 1] == "'":
                    current.append(ch)
                    current.append(actions[i + 1])
                    i += 2
                    continue
                in_single_quote = not in_single_quote
                current.append(ch)
                i += 1
                continue

            # 큰따옴표 식별자 처리
            if not in_single_quote and ch == '"':
                in_double_quote = not in_double_quote
                current.append(ch)
                i += 1
                continue

            if not in_single_quote and not in_double_quote:
                if ch == '(':
                    paren_depth += 1
                elif ch == ')' and paren_depth > 0:
                    paren_depth -= 1
                elif ch == ',' and paren_depth == 0:
                    action = ''.join(current).strip()
                    if action:
                        result.append(action)
                    current = []
                    i += 1
                    continue

            current.append(ch)
            i += 1

        tail = ''.join(current).strip()
        if tail:
            result.append(tail)

        return result

    def _parse_columns(self, columns_def: str) -> List[Dict[str, Any]]:
        """컬럼 정의 파싱

        Args:
            columns_def: CREATE TABLE 내부의 컬럼 정의 문자열

        Returns:
            컬럼 정보 리스트
        """
        columns = []

        # 각 줄을 파싱 (CONSTRAINT, PRIMARY KEY, FOREIGN KEY 줄 제외)
        for line in columns_def.split("\n"):
            line = line.strip()
            if not line:
                continue

            # 제약조건 정의 줄 처리
            upper_line = line.upper()

            # 테이블 레벨 UNIQUE 제약조건에서 컬럼명 추출 (예: UNIQUE(isbn))
            if upper_line.startswith("UNIQUE"):
                unique_cols_match = re.search(r'UNIQUE\s*\(([^)]+)\)', line, re.IGNORECASE)
                if unique_cols_match:
                    for uc in unique_cols_match.group(1).split(","):
                        uc = uc.strip().strip('"').strip("'")
                        for col in columns:
                            if col["name"].upper() == uc.upper():
                                col["unique"] = True
                continue

            if (upper_line.startswith("CONSTRAINT") or
                upper_line.startswith("PRIMARY") or
                upper_line.startswith("FOREIGN") or
                upper_line.startswith("CHECK")):
                continue

            # 인라인 주석 추출 (-- 이후의 내용)
            comment = ""
            comment_match = re.search(r'--\s*(.+)$', line)
            if comment_match:
                comment = comment_match.group(1).strip()
                # 주석 부분 제거
                line = line[:comment_match.start()].strip()

            # 마지막 쉼표 제거
            line = line.rstrip(",").strip()

            if not line:
                continue

            # 컬럼 정의 파싱
            col_match = re.match(
                r'"?(\w+)"?\s+'  # 컬럼명
                r'([A-Za-z_]+(?:\s*\([^)]*\))?)'  # 데이터 타입
                r'(.*?)$',  # 나머지 (NOT NULL, DEFAULT 등)
                line,
                re.IGNORECASE
            )

            if col_match:
                col_name = col_match.group(1)
                col_type = col_match.group(2).strip()
                constraints = col_match.group(3).upper() if col_match.group(3) else ""

                raw_constraints = col_match.group(3).strip() if col_match.group(3) else ""
                nullable = "NOT NULL" not in constraints
                is_primary = "PRIMARY KEY" in constraints or "PRIMARY" in constraints
                is_unique = "UNIQUE" in constraints

                # DEFAULT 값 추출
                default_value = None
                default_match = re.search(r"DEFAULT\s+('(?:[^']*)'|[^\s,]+)", raw_constraints, re.IGNORECASE)
                if default_match:
                    default_value = default_match.group(1).strip()

                col_data = {
                    "name": col_name,
                    "dtype": col_type,
                    "nullable": nullable,
                    "comment": comment,
                    "primary_key": is_primary,
                }
                if is_unique:
                    col_data["unique"] = True
                if default_value is not None:
                    col_data["default_value"] = default_value

                columns.append(col_data)

        return columns


# 싱글톤 인스턴스
_parser_instance: Optional[DDLParser] = None


def get_ddl_parser() -> DDLParser:
    """DDL 파서 싱글톤 인스턴스 반환"""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = DDLParser()
    return _parser_instance


def parse_ddl(ddl_content: str) -> Dict[str, Any]:
    """DDL 파싱 (편의 함수)

    Args:
        ddl_content: DDL 파일 내용

    Returns:
        파싱 결과 (LLM 파서와 동일한 형식)
    """
    return get_ddl_parser().parse(ddl_content)


# =====================================================================
# Section 3: DDL 처리 + Neo4j 반영 (from ddl_processor.py)
# =====================================================================


async def process_ddl(
    ddl_path: str,
    client: Neo4jClient,
    file_name: str,
    orchestrator: Any,
    cypher_lock: asyncio.Lock,
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
        yield emit_message(f"   ⚡ 정규식 파서 사용 (정적 파싱)")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=file_base_progress + int(file_range * 0.1),
            details={"mode": "regex", "tokens": total_tokens}
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
            yield emit_message(f"   ✅ 파싱 완료: {table_count}개 테이블 ({preview})")
            yield emit_phase_event(
                phase_num=0,
                phase_name="DDL 처리",
                status="in_progress",
                progress=parsing_end,
                details={"tables_parsed": table_count, "mode": "regex"}
            )
    except Exception as e:
        if emit_progress:
            yield emit_message(f"   ❌ 정규식 파싱 실패: {str(e)[:80]}")
        raise RuntimeError(f"DDL 정규식 파싱 실패: {e}")

    parsed = {"analysis": all_parsed_results}
    # target이 프로그래밍 언어(c, java 등)일 때는 DB 기본값 사용
    _LANG_TARGETS = {"c", "java", "python", "go", "kotlin", "scala", "rust"}
    _raw_target = (orchestrator.target or "postgres").lower()
    db_name = "postgres" if _raw_target in _LANG_TARGETS else _raw_target
    name_case = getattr(orchestrator, 'name_case', 'original')

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
        schema = apply_name_case(parsed_schema if parsed_schema else "public", name_case)
        parsed_name = apply_name_case(parsed_name, name_case)

        if schema and schema.lower() != 'public':
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
        yield emit_message(f"   💾 UNWIND 배치 저장 시작: {ddl_stats['tables']}개 테이블, {ddl_stats['columns']}개 컬럼, {ddl_stats['fks']}개 FK")
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
            yield emit_message(f"      📦 [1/7] 스키마 {len(schemas_data)}개 생성 중...")
        schema_query = SCHEMA_MERGE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(schema_query, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if schemas_data and datasource_name:
        if emit_progress:
            yield emit_message(f"      🔗 DataSource({datasource_name}) 노드 생성 및 스키마 연결 중...")
        ds_create_query = DATASOURCE_CREATE_QUERY
        async with cypher_lock:
            await client.execute_queries([ds_create_query], {"datasource_name": datasource_name})
        ds_schema_query = DATASOURCE_SCHEMA_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(ds_schema_query, schemas_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if tables_data:
        if emit_progress:
            yield emit_message(f"      📦 [2/7] 테이블 {len(tables_data)}개 생성 중...")
        table_query = TABLE_MERGE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(table_query, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if tables_data:
        if emit_progress:
            yield emit_message(f"      📦 [3/7] 테이블-스키마 관계 {len(tables_data)}개 생성 중...")
        belongs_query = BELONGS_TO_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(belongs_query, tables_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if columns_data:
        if emit_progress:
            yield emit_message(f"      📦 [4/7] 컬럼 {len(columns_data)}개 생성 중...")
        column_query = COLUMN_MERGE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(column_query, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node

    if columns_data:
        if emit_progress:
            yield emit_message(f"      📦 [5/7] 테이블-컬럼 관계 {len(columns_data)}개 생성 중...")
        has_column_query = HAS_COLUMN_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(has_column_query, columns_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if fks_data:
        if emit_progress:
            yield emit_message(f"      📦 [6/7] FK 관계 {len(fks_data)}개 생성 중...")
        ref_tables_query = FK_REF_TABLE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(ref_tables_query, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        fk_query = FK_TO_TABLE_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(fk_query, fks_data)
        for node in result.get("Nodes", []):
            all_nodes[node.get("Node ID")] = node
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

        fk_col_query = FK_TO_COLUMN_QUERY
        async with cypher_lock:
            result = await client.run_batch_unwind(fk_col_query, fks_data)
        for rel in result.get("Relationships", []):
            all_relationships[rel.get("Relationship ID")] = rel

    if emit_progress:
        yield emit_message(f"   ✅ UNWIND 배치 저장 완료: {len(all_nodes)}개 노드, {len(all_relationships)}개 관계")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_end,
            details={"step": "unwind_completed", "nodes_created": len(all_nodes), "relationships_created": len(all_relationships)}
        )

    graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
    if emit_progress:
        yield emit_message(f"   ✅ Neo4j 저장 완료: {len(graph['Nodes'])}개 노드, {len(graph['Relationships'])}개 관계 생성")
        yield emit_phase_event(
            phase_num=0,
            phase_name="DDL 처리",
            status="in_progress",
            progress=saving_end,
            details={"step": "neo4j_saved", "tables": ddl_stats['tables'], "columns": ddl_stats['columns'], "fks": ddl_stats['fks'], "nodes_created": len(graph['Nodes']), "relationships_created": len(graph['Relationships'])}
        )

    # 그래프 데이터를 bytes로 직접 스트리밍 (프론트에서 즉시 렌더링)
    from util.stream_emitter import emit_data
    yield emit_data(graph=graph)

    log_process("ANALYZE", "DDL", f"DDL 처리 완료: {file_name} (T:{ddl_stats['tables']}, C:{ddl_stats['columns']}, FK:{ddl_stats['fks']})")


# =====================================================================
# Section 4: DDL 처리 루프 (from ddl_phase.py)
# =====================================================================


async def run_ddl_processing(
    *,
    orchestrator: Any,
    client: Any,
    cypher_lock: Any,
    ddl_schemas: set,
    ddl_table_metadata: dict,
    datasource_name: str = "",
    skip_message: str = "📋 DDL 파일 없음 — DDL 처리 생략",
    on_file_start: Optional[Any] = None,
) -> AsyncGenerator[bytes, None]:
    """DDL 파일 처리 공통 루프.

    Args:
        orchestrator: 오케스트레이터 (dirs, datasource_name 등)
        client: Neo4j 클라이언트
        cypher_lock: asyncio.Lock
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
        yield emit_message(skip_message)
        return

    ddl_dir = orchestrator.dirs.get("ddl", "")
    total_files = len(ddl_files)
    ds_name = datasource_name or getattr(orchestrator, "datasource_name", "") or ""

    yield emit_message(f"📋 DDL 파일 {total_files}개 처리 시작")

    for idx, fname in enumerate(ddl_files):
        ddl_path = os.path.join(ddl_dir, fname)
        file_base = int((idx / total_files) * 100)
        file_end = int(((idx + 1) / total_files) * 100)

        if on_file_start:
            yield on_file_start(idx + 1, total_files, fname)
        else:
            yield emit_message(f"  📄 [{idx + 1}/{total_files}] {fname}")

        try:
            async for chunk in process_ddl(
                ddl_path=ddl_path,
                client=client,
                file_name=fname,
                orchestrator=orchestrator,
                cypher_lock=cypher_lock,
                ddl_schemas=ddl_schemas,
                ddl_table_metadata=ddl_table_metadata,
                emit_progress=True,
                file_base_progress=file_base,
                file_end_progress=file_end,
                datasource_name=ds_name,
            ):
                if isinstance(chunk, bytes):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:0:DDL] DDL 처리 실패 | file=%s error=%s", fname, e, exc_info=True)
            yield emit_message(f"  ⚠️ DDL 처리 실패: {fname} ({str(e)[:60]})")

        yield emit_phase_event(0, "DDL 처리", "running", file_end)

    yield emit_message(f"✅ DDL 처리 완료 | schemas={len(ddl_schemas)} tables={len(ddl_table_metadata)}")
