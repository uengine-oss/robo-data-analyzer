"""DDL 정규식 파서 - LLM 없이 DDL 구조 분석

PostgreSQL/Oracle DDL 파일을 정규식으로 파싱하여 테이블/컬럼/FK 정보를 추출합니다.
LLM 호출 없이 즉시 처리되어 대용량 DDL 파일도 빠르게 분석할 수 있습니다.

지원하는 구문:
- CREATE TABLE IF NOT EXISTS SCHEMA."TABLE_NAME" (column_definitions);
- COMMENT ON TABLE SCHEMA."TABLE_NAME" IS '설명';
- COMMENT ON COLUMN SCHEMA."TABLE_NAME"."COLUMN_NAME" IS '설명';
- ALTER TABLE ... ADD PRIMARY KEY (columns);
- ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY ... REFERENCES ...;
- CREATE TABLE 내 인라인 FOREIGN KEY (column) REFERENCES table(column);
- CREATE TABLE 내 인라인 PRIMARY KEY (columns);
- 컬럼 정의 끝의 인라인 주석 (-- 설명);
"""

import re
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


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

            # 제약조건 정의 줄 스킵
            upper_line = line.upper()
            if (upper_line.startswith("CONSTRAINT") or
                upper_line.startswith("PRIMARY") or
                upper_line.startswith("FOREIGN") or
                upper_line.startswith("UNIQUE") or
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

                nullable = "NOT NULL" not in constraints
                is_primary = "PRIMARY KEY" in constraints or "PRIMARY" in constraints

                columns.append({
                    "name": col_name,
                    "dtype": col_type,
                    "nullable": nullable,
                    "comment": comment,
                    "primary_key": is_primary,
                })

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
