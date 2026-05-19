"""PLSQLParser — PL/SQL 파서 (regex + 구조 인식).

추출 대상:
- CREATE [OR REPLACE] PROCEDURE / FUNCTION (최상위)
- PACKAGE BODY 안 PROCEDURE / FUNCTION (IS/AS 다음)
- PROCEDURE / FUNCTION 안 nested range (DML — SELECT/INSERT/UPDATE/DELETE/MERGE)

설계:
- procedure / function 식별 = regex (PL/SQL 키워드 단순).
- nested DML range 끝 = ``_find_statement_end`` 가 문자열 리터럴 / 괄호 깊이 추적해
  ``;`` 정확히 찾음. (이전 "10 줄 휴리스틱" 폐기.)

ANTLR grammar 도입 (P2) 시 이 파일 통째 교체 — 외부 인터페이스
(``parse() / ParseResult``) 는 동일 유지.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from data_models.graph_edges import ParentOf, Uses
from data_models.graph_nodes import Function, Module
from shared.logging_and_stream.logger import log_warning


# CREATE [OR REPLACE] PROCEDURE / FUNCTION (최상위 — 스크립트 파일)
_PROC_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(PROCEDURE|FUNCTION)\s+([\w$#.]+)\s*(\([^)]*\))?",
    re.IGNORECASE,
)

# PACKAGE / PACKAGE BODY
_PKG_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE(?:\s+BODY)?\s+([\w$#.]+)",
    re.IGNORECASE,
)

# 패키지 안 PROCEDURE / FUNCTION ("PROCEDURE name(...) IS" 형식)
_INNER_PROC_RE = re.compile(
    r"\b(PROCEDURE|FUNCTION)\s+([\w$#]+)\s*(\([^)]*\))?\s+(?:RETURN\s+\w+\s+)?(?:IS|AS)\b",
    re.IGNORECASE,
)

# DML — nested range 의 시작 신호
_DML_RE = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\b",
    re.IGNORECASE,
)

# END [name];  — 블록 끝 식별
_END_RE = re.compile(r"\bEND(\s+\w+)?\s*;", re.IGNORECASE)


@dataclass
class ParseResult:
    functions: list[Function] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    parent_of_edges: list[ParentOf] = field(default_factory=list)
    uses_edges: list[Uses] = field(default_factory=list)


class PLSQLParser:
    """PL/SQL 파서 — regex + 구조 인식.

    procedure / function / nested DML range 모두 ``Function`` 노드.
    nested 는 ``parent_id`` 채움 + ``PARENT_OF`` 엣지 등록.
    """

    supported_extensions: tuple[str, ...] = ("sql", "pks", "pkb", "pls", "ddl")

    async def parse(self, path: Path) -> ParseResult:
        result = ParseResult()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError) as e:
            log_warning(
                "analysis_flow.step2_parse_source.plsql_parser",
                "read_file",
                "PL/SQL 파일 읽기 실패 — 이 파일 건너뜀",
                path=str(path),
                error=f"{type(e).__name__}: {e}",
            )
            return result

        file_id = path.stem
        seen_ids: set[str] = set()

        # PACKAGE / PACKAGE BODY → Module (라인 범위 포함)
        for m in _PKG_RE.finditer(text):
            pkg_name = m.group(1)
            pkg_start_line = text[: m.start()].count("\n") + 1
            pkg_end_line = _find_block_end(text, m.start(), pkg_start_line)
            result.modules.append(Module(
                module_id=pkg_name,
                name=pkg_name.split(".")[-1],
                file_path=str(path),
                start_line=pkg_start_line,
                end_line=pkg_end_line,
            ))

        # 파일 자체도 Module (file-level fallback)
        result.modules.append(Module(
            module_id=file_id,
            name=file_id,
            file_path=str(path),
        ))

        # 1) CREATE PROCEDURE / FUNCTION (최상위)
        for m in _PROC_RE.finditer(text):
            kind = m.group(1).upper()
            name = m.group(2)
            params = m.group(3) or "()"
            start_line = text[: m.start()].count("\n") + 1
            end_line = _find_block_end(text, m.start(), start_line)
            fqn = f"{file_id}:{name}:{start_line}"
            if fqn in seen_ids:
                continue
            seen_ids.add(fqn)
            result.functions.append(Function(
                function_id=fqn,
                signature=f"{kind} {name}{params}",
                file_path=str(path),
                start_line=start_line,
                end_line=end_line,
            ))

        # 2) PACKAGE BODY 안 PROCEDURE / FUNCTION
        for m in _INNER_PROC_RE.finditer(text):
            kind = m.group(1).upper()
            name = m.group(2)
            params = m.group(3) or "()"
            start_line = text[: m.start()].count("\n") + 1
            end_line = _find_block_end(text, m.start(), start_line)
            fqn = f"{file_id}:{name}:{start_line}"
            if fqn in seen_ids:
                continue
            seen_ids.add(fqn)
            result.functions.append(Function(
                function_id=fqn,
                signature=f"{kind} {name}{params}",
                file_path=str(path),
                start_line=start_line,
                end_line=end_line,
            ))

        # 3) 각 procedure 본문 안 DML range → nested Function (parent_id 부착).
        # DML 끝은 ``_find_statement_end`` 가 문자열 리터럴 / 중첩 괄호 인식해서 정확히 추적.
        lines = text.splitlines()
        for parent_fn in list(result.functions):
            body_lines = lines[parent_fn.start_line - 1 : parent_fn.end_line]
            body = "\n".join(body_lines)
            for m in _DML_RE.finditer(body):
                dml_kind = re.split(r"\s+", m.group(1).upper(), 1)[0]
                inner_start_offset = body[: m.start()].count("\n")
                inner_start = parent_fn.start_line + inner_start_offset

                end_pos = _find_statement_end(body, m.start())
                if end_pos < 0:
                    inner_end_offset = body.count("\n", m.start())
                else:
                    inner_end_offset = body[: end_pos + 1].count("\n")
                inner_end = parent_fn.start_line + inner_end_offset

                inner_id = f"{parent_fn.function_id}:dml:{inner_start}"
                if inner_id in seen_ids:
                    continue
                seen_ids.add(inner_id)
                result.functions.append(Function(
                    function_id=inner_id,
                    signature=f"{dml_kind} range",
                    file_path=str(path),
                    start_line=inner_start,
                    end_line=inner_end,
                    parent_id=parent_fn.function_id,
                ))
                result.parent_of_edges.append(ParentOf(
                    parent_function_id=parent_fn.function_id,
                    child_function_id=inner_id,
                ))

        return result


def _find_block_end(text: str, start_offset: int, start_line: int) -> int:
    """BEGIN ... END; 블록 끝 추정 (가장 먼저 만나는 END 또는 fallback 100줄)."""
    tail = text[start_offset:]
    m_end = _END_RE.search(tail)
    if m_end is None:
        return start_line + 100
    return start_line + tail[: m_end.end()].count("\n")


def _find_statement_end(text: str, start: int) -> int:
    """``start`` 부터 statement 종료 ``;`` 인덱스 찾기 (정확).

    문자열 리터럴 / 괄호 안의 ``;`` 는 건너뜀.
    못 찾으면 ``-1`` (호출자가 block 끝까지 fallback).

    PL/SQL 문자열 리터럴:
    - ``'...'`` — 작은따옴표 ``''`` 가 escape.
    - PL/SQL q'-string (``q'[...]'``) 은 단순화로 미지원 (드문 케이스).
    """
    i = start
    n = len(text)
    paren_depth = 0
    in_string = False

    while i < n:
        c = text[i]
        if in_string:
            if c == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if c == "'":
            in_string = True
            i += 1
            continue
        if c == "(":
            paren_depth += 1
        elif c == ")":
            paren_depth = max(paren_depth - 1, 0)
        elif c == ";" and paren_depth == 0:
            return i
        i += 1
    return -1
