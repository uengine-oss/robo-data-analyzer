"""CallRegexLinker — PL/SQL PKG.FUNC + BARE FUNC regex (LLM 0회).

DBMS 의 stage2_extract_calls 가 사용 (Framework 는 call_classifier 결정론 분류).

DBMS_/UTL_/SYS_ 내장 패키지 = external.
PL/SQL 예약어 + 내장 함수 = 호출 아님 (필터).
주석 (블록/라인) 제거 후 매칭.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from data_models.graph_edges import Calls
from data_models.graph_nodes import Function


# 식별자 패턴 — PL/SQL: A-Z a-z 0-9 _ $ # (한글 제외)
_IDENT = r"[A-Za-z_$#][A-Za-z0-9_$#]*"

# PKG.FUNC( 호출
_PKG_FUNC_RE = re.compile(
    rf"\b({_IDENT})\s*\.\s*({_IDENT})\s*\(",
    re.ASCII,
)

# BARE FUNC( 호출 — 줄 시작 / 연산자 / 공백 뒤
_BARE_FUNC_RE = re.compile(
    rf"(?:^|[\s(,:=])\s*({_IDENT})\s*\(",
    re.MULTILINE | re.ASCII,
)

# 주석 제거 (단순 — ANTLR 이 정확하지만 일단)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

# 내장 패키지 prefix → external 처리
_BUILTIN_PREFIXES = ("DBMS_", "UTL_", "SYS_", "ORA_", "OWA_", "STANDARD")

# PL/SQL 예약어 + 자주 쓰이는 내장 함수 → 호출 아님
_PLSQL_KEYWORDS = {
    "IF", "THEN", "ELSE", "ELSIF", "END", "LOOP", "FOR", "WHILE",
    "BEGIN", "DECLARE", "EXCEPTION", "WHEN", "CASE", "RETURN", "RAISE",
    "COMMIT", "ROLLBACK", "SAVEPOINT", "NULL", "AND", "OR", "NOT", "IS", "IN",
    "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "FROM", "INTO", "VALUES",
    "WHERE", "SET", "GROUP", "ORDER", "HAVING", "JOIN", "ON", "AS", "BY",
    # 내장 함수
    "CAST", "NVL", "NVL2", "DECODE", "COALESCE", "GREATEST", "LEAST",
    "TO_CHAR", "TO_DATE", "TO_NUMBER", "TO_TIMESTAMP", "TRUNC", "ROUND",
    "SUBSTR", "LENGTH", "UPPER", "LOWER", "TRIM", "LPAD", "RPAD", "REPLACE",
    "SYSDATE", "CURRENT_DATE", "CURRENT_TIMESTAMP",
    "COUNT", "SUM", "AVG", "MIN", "MAX", "ROW_NUMBER", "RANK",
}


class CallRegexLinker:
    """PL/SQL Call regex → ``Calls`` 엣지."""

    async def link(
        self,
        project: Any,
        functions: list[Function],
    ) -> list[Calls]:
        if not functions:
            return []

        # internal 후보 — 같은 코드베이스 함수 이름 → function_id
        internal_names: dict[str, str] = {}
        for fn in functions:
            name = _extract_name(fn).upper()
            internal_names.setdefault(name, fn.function_id)

        edges: list[Calls] = []
        seen: set[tuple[str, str]] = set()

        # 파일별로 본문 읽고 주석 제거
        files_text: dict[str, str] = {}
        for fn in functions:
            if fn.file_path not in files_text:
                try:
                    raw = Path(fn.file_path).read_text(encoding="utf-8", errors="ignore")
                except (OSError, UnicodeDecodeError):
                    continue
                cleaned = _BLOCK_COMMENT_RE.sub("", raw)
                cleaned = _LINE_COMMENT_RE.sub("", cleaned)
                files_text[fn.file_path] = cleaned

        for fn in functions:
            text = files_text.get(fn.file_path)
            if not text:
                continue
            lines = text.splitlines()
            body = "\n".join(lines[max(fn.start_line - 1, 0) : fn.end_line])

            # PKG.FUNC
            for m in _PKG_FUNC_RE.finditer(body):
                pkg = m.group(1).upper()
                name = m.group(2).upper()
                if pkg in _PLSQL_KEYWORDS:
                    continue
                if any(pkg.startswith(p) for p in _BUILTIN_PREFIXES):
                    callee_id = f"__external__:{pkg}.{name}"
                    scope: str = "external"
                else:
                    callee_id = internal_names.get(name, f"__external__:{pkg}.{name}")
                    scope = "internal" if name in internal_names else "external"

                key = (fn.function_id, callee_id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(Calls(
                    caller_function_id=fn.function_id,
                    callee_function_id=callee_id,
                    scope=scope,
                ))

            # BARE FUNC
            for m in _BARE_FUNC_RE.finditer(body):
                name = m.group(1).upper()
                if name in _PLSQL_KEYWORDS:
                    continue
                callee_id = internal_names.get(name)
                if callee_id is None:
                    continue  # external bare — 너무 노이즈
                key = (fn.function_id, callee_id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(Calls(
                    caller_function_id=fn.function_id,
                    callee_function_id=callee_id,
                    scope="internal",
                ))
        return edges


def _extract_name(fn: Function) -> str:
    """function_id 또는 signature 에서 이름 추출."""
    sig = fn.signature.split("(", 1)[0].strip()
    if "." in sig:
        return sig.split(".")[-1]
    if sig:
        return sig
    return fn.function_id.split(":")[-1].split(".")[-1]
