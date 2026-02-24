"""SQL Analyzer - Extract table names and column names from SQL text.

iBATIS/MyBatis XML mapper 및 일반 SQL에서 테이블/컬럼 참조 추출.
deep-agents-modernizer 로직 기반.
"""

from __future__ import annotations

import re
from typing import Any

# FROM/INTO/UPDATE/JOIN 뒤의 테이블 참조 (스키마.테이블)
TABLE_PATTERN = re.compile(
    r'\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Za-z0-9_]+\.[A-Za-z0-9_\u3131-\uD79D]+)',
    re.IGNORECASE
)

# 스키마 없이 테이블명만
TABLE_SIMPLE_PATTERN = re.compile(
    r'\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Za-z0-9_\u3131-\uD79D]+)',
    re.IGNORECASE
)

# SELECT 컬럼 영역
COLUMN_IN_SELECT_PATTERN = re.compile(
    r'(?:SELECT|select)(?:\s+/\*.*?\*/\s*)?\s+(.*?)(?:\bFROM\b)',
    re.DOTALL | re.IGNORECASE
)

# INSERT INTO 테이블(컬럼...)
COLUMN_IN_INSERT_PATTERN = re.compile(
    r'INTO\s+[A-Za-z0-9_.\u3131-\uD79D]+\s*\((.*?)\)',
    re.DOTALL | re.IGNORECASE
)


def extract_tables_from_sql(sql_text: str) -> list[dict[str, str]]:
    """Extract table names and schemas from SQL text.

    Returns:
        [{"schema": "INST1", "table": "TSKEEKR01", "alias": ""}, ...]
    """
    tables: list[dict[str, str]] = []
    seen: set[str] = set()

    # Qualified: SCHEMA.TABLE
    for match in TABLE_PATTERN.finditer(sql_text):
        full_ref = match.group(1)
        parts = full_ref.split(".", 1)
        if len(parts) == 2:
            schema, table = parts
            key = f"{schema}.{table}"
            if key not in seen:
                seen.add(key)
                tables.append({"schema": schema, "table": table, "alias": ""})

    # Simple table name (no schema)
    for match in TABLE_SIMPLE_PATTERN.finditer(sql_text):
        table = match.group(1)
        if "." in table:
            continue  # Already captured above
        if table.upper() in ("SET", "WHERE", "AND", "OR", "ON", "VALUES", "ORDER", "GROUP", "HAVING", "INNER", "LEFT", "RIGHT", "OUTER", "CROSS"):
            continue
        if table not in seen:
            seen.add(table)
            tables.append({"schema": "", "table": table, "alias": ""})

    return tables


def extract_columns_from_sql(sql_text: str, sql_type: str) -> list[str]:
    """Extract column names from SQL text based on SQL type."""
    columns: list[str] = []

    if sql_type.lower() == "select":
        match = COLUMN_IN_SELECT_PATTERN.search(sql_text)
        if match:
            col_section = match.group(1)
            columns.extend(_parse_column_list(col_section))

    elif sql_type.lower() == "insert":
        match = COLUMN_IN_INSERT_PATTERN.search(sql_text)
        if match:
            col_section = match.group(1)
            columns.extend(c.strip() for c in col_section.split(",") if c.strip())

    elif sql_type.lower() == "update":
        set_match = re.search(r'\bSET\b\s+(.*?)(?:\bWHERE\b|$)', sql_text, re.DOTALL | re.IGNORECASE)
        if set_match:
            set_clause = set_match.group(1)
            for part in set_clause.split(","):
                eq_idx = part.find("=")
                if eq_idx > 0:
                    col = part[:eq_idx].strip()
                    if col:
                        columns.append(col)

    return columns


def _parse_column_list(col_section: str) -> list[str]:
    """Parse SELECT column list (handles aliases, functions, Korean names)."""
    result: list[str] = []
    for part in col_section.split(","):
        part = part.strip()
        if not part or part == "*":
            continue
        # Alias: col AS alias -> take col
        as_match = re.search(r'\bAS\s+[\w\u3131-\uD79D]+$', part, re.IGNORECASE)
        if as_match:
            part = part[: as_match.start()].strip()
        # Dot: table.col -> take col
        if "." in part and not part.startswith("("):
            part = part.split(".", 1)[-1].strip()
        # Function: func(col) -> skip or extract
        if "(" in part and part.strip().endswith(")"):
            continue  # Skip function calls
        if part:
            result.append(part)
    return result
