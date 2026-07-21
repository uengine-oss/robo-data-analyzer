"""Conservative SQL text to table-reference extraction."""
from __future__ import annotations

from dataclasses import dataclass
import re


_XML_TAG = re.compile(r"<[^>]+>")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_SQL_STRING = re.compile(r"'(?:''|[^'])*'")
_IDENT = r"[A-Za-z_][\w$#]*"
_QUALIFIED = rf"{_IDENT}(?:\.{_IDENT})?"
_WRITE_PATTERNS = (
    ("INSERT", re.compile(rf"\bINSERT\s+INTO\s+({_QUALIFIED})", re.IGNORECASE)),
    ("UPDATE", re.compile(rf"\bUPDATE\s+({_QUALIFIED})\s+SET\b", re.IGNORECASE)),
    ("DELETE", re.compile(rf"\bDELETE\s+FROM\s+({_QUALIFIED})", re.IGNORECASE)),
    ("MERGE", re.compile(rf"\bMERGE\s+INTO\s+({_QUALIFIED})", re.IGNORECASE)),
)
_READ_PATTERNS = (
    re.compile(rf"\bUSING\s+({_QUALIFIED})(?!\s*\()", re.IGNORECASE),
    re.compile(rf"\bFROM\s+({_QUALIFIED}(?:\s+(?:AS\s+)?{_IDENT})?(?:\s*,\s*{_QUALIFIED}(?:\s+(?:AS\s+)?{_IDENT})?)*)", re.IGNORECASE),
    re.compile(rf"\bJOIN\s+({_QUALIFIED})", re.IGNORECASE),
)
_CTE_DEF = re.compile(
    rf"(?:\bWITH(?:\s+RECURSIVE)?|,)\s+({_IDENT})(?:\s*\([^)]*\))?\s+AS\s*\(",
    re.IGNORECASE,
)
_STOPWORDS = frozenset({
    "DUAL", "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "ON", "IN", "AS",
    "VALUES", "SET", "INTO", "JOIN", "INNER", "LEFT", "RIGHT", "OUTER", "FULL", "CROSS",
    "UNION", "ALL", "GROUP", "ORDER", "BY", "HAVING", "LIMIT", "OFFSET", "DISTINCT",
    "INSERT", "UPDATE", "DELETE", "MERGE", "USING", "WHEN", "THEN", "ELSE", "END", "CASE",
    "EXISTS", "BETWEEN", "LIKE", "IS", "NULL", "WITH", "CONNECT", "START", "PRIOR",
})


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str
    op: str

    @property
    def relation(self) -> str:
        return "WRITES" if self.op in {"INSERT", "UPDATE", "DELETE", "MERGE"} else "READS"


def sql_skeleton(text: str) -> str:
    text = _XML_TAG.sub(" ", text)
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    return _SQL_STRING.sub(" ", text)


def _split_identifier(token: str) -> tuple[str, str] | None:
    token = token.strip().rstrip("(,")
    if not token or token.upper() in _STOPWORDS or not re.fullmatch(_QUALIFIED, token):
        return None
    if "." in token:
        schema, name = token.split(".", 1)
        return schema.upper(), name.upper()
    return "", token.upper()


def extract_table_refs(sql_text: str) -> list[TableRef]:
    text = sql_skeleton(sql_text)
    cte_names = {match.group(1).upper() for match in _CTE_DEF.finditer(text)}
    read_text = text
    for _op, pattern in _WRITE_PATTERNS:
        read_text = pattern.sub(lambda match: " " * len(match.group(0)), read_text)
    output: list[TableRef] = []
    seen: set[tuple[str, str, str]] = set()

    def add(token: str, op: str) -> None:
        parsed = _split_identifier(token)
        if parsed is None:
            return
        schema, name = parsed
        if not schema and name in cte_names:
            return
        identity = (schema, name, op)
        if identity not in seen:
            seen.add(identity)
            output.append(TableRef(schema, name, op))

    for op, pattern in _WRITE_PATTERNS:
        for match in pattern.finditer(text):
            add(match.group(1), op)
    for pattern in _READ_PATTERNS:
        for match in pattern.finditer(read_text):
            for part in match.group(1).split(","):
                token = part.strip().split()[0] if part.strip() else ""
                add(token, "SELECT")
    return output
