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
            
            schema, table_name = self._parse_table_name(full_name)
            table_key = f"{schema}.{table_name}".lower()
            
            columns = self._parse_columns(columns_def)
            
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
                    "comment": "",
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
        
        # 4. PRIMARY KEY 파싱
        for match in self.pk_pattern.finditer(ddl_content):
            full_name = match.group(1)
            pk_columns = [self._strip_quotes(c.strip()) for c in match.group(2).split(",")]
            
            schema, table_name = self._parse_table_name(full_name)
            table_key = f"{schema}.{table_name}".lower()
            
            if table_key in tables:
                tables[table_key]["primaryKeys"] = pk_columns
        
        # 5. FOREIGN KEY 파싱
        for match in self.fk_pattern.finditer(ddl_content):
            src_table = match.group(1)
            src_columns = [self._strip_quotes(c.strip()) for c in match.group(2).split(",")]
            ref_table = match.group(3)
            ref_columns = [self._strip_quotes(c.strip()) for c in match.group(4).split(",")]
            
            src_schema, src_name = self._parse_table_name(src_table)
            ref_schema, ref_name = self._parse_table_name(ref_table)
            table_key = f"{src_schema}.{src_name}".lower()
            
            if table_key in tables:
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

