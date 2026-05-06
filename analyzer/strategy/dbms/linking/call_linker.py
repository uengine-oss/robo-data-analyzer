"""DBMS 결정론 CALLS 관계 빌더 (Phase 2-a).

AST 순회 + regex 로 callee 이름 추출해 CALLS 쿼리 생성.
LLM 미사용.

대상 노드 필터:
  - node_type == "CALL" (명시적 호출)
  - node.initializer_contains_method_call == True
    (antlr-parser 가 ASSIGNMENT/IF/ELSIF/CASE/RETURN 에 부여한 플래그)

제외:
  - DBMS_SYSTEM_PACKAGES (DBMS_OUTPUT, UTL_FILE 등 내장)
  - SELECT/INSERT/UPDATE/MERGE 내부 함수 호출 (대부분 내장 함수)
  - EXECUTE IMMEDIATE 동적 호출
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from analyzer.strategy.dbms.common.domain.analysis_constants import (
    DBMS_SYSTEM_PACKAGES,
    PROCEDURE_TYPES,
)
from analyzer.strategy.dbms.common.cypher.graph_queries import (
    build_calls_external_pkg_queries,
    build_calls_external_placeholder_queries,
    build_calls_internal_queries,
)
from analyzer.strategy.dbms.common.models.node_models import StatementNode

logger = logging.getLogger(__name__)


# ── callee 추출 regex (Framework call_linker 와 동일 패턴) ─────────────────
# PL/SQL identifier 는 ASCII 영문/숫자/_/$/# 만 — re.ASCII 로 \b/\w 한글 매칭 차단.
# (한글 주석에 "단어(" 패턴이 있어도 callee 로 오인하지 않음 — call_linker 가
#  한국어 키워드를 EXTERNAL placeholder PROCEDURE 로 환각하던 결함 수정)
# PKG.FUNC( — 스키마/패키지 접두사 있는 호출
_OBJ_METHOD_PATTERN = re.compile(r"\b([A-Za-z_$#][A-Za-z0-9_$#]*)\s*\.\s*([A-Za-z_$#][A-Za-z0-9_$#]*)\s*\(", re.ASCII)
# := FUNC( 또는 일반 FUNC( — 단독 호출
_BARE_METHOD_PATTERN = re.compile(r"(?:^|:=|[\s(,])([A-Za-z_$#][A-Za-z0-9_$#]*)\s*\(", re.ASCII)

# 코드에서 주석 (-- ..., /* ... */) 제거용 — 한글/영어 주석 안의 함수형 패턴이
# CALLS 환각으로 이어지지 않도록 LLM 호출 전 필터.
_LINE_COMMENT_PATTERN = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)

# regex 로 잡히지만 호출이 아닌 PL/SQL 예약어
_PLSQL_KEYWORDS: Set[str] = {
    "IF", "THEN", "ELSE", "ELSIF", "CASE", "WHEN", "END", "LOOP", "FOR", "WHILE",
    "BEGIN", "EXCEPTION", "RETURN", "NULL", "TRUE", "FALSE",
    "AND", "OR", "NOT", "IN", "IS", "AS", "LIKE", "BETWEEN",
    "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "FROM", "WHERE", "INTO",
    "VALUES", "SET", "JOIN", "ON", "GROUP", "ORDER", "BY", "HAVING", "UNION",
    "CURSOR", "OPEN", "CLOSE", "FETCH", "RAISE", "COMMIT", "ROLLBACK",
    "USING", "WITH", "DECLARE",
}


def build_calls_queries(
    nodes: List[StatementNode],
    full_directory: str,
    file_name: str,
) -> List[Dict[str, Any]]:
    """CALLS 관계 쿼리 리스트 생성 — UNWIND 통합 (3 종류 × src label 별 그룹).

    종류별로 items 누적 후 1번씩 build_calls_*_queries 호출.
    """
    if not nodes:
        return []

    # 같은 파일 내 PROCEDURE/FUNCTION 이름 수집 (internal 판정용)
    internal_names = _collect_internal_names(nodes)

    pkg_items: List[Dict[str, Any]] = []
    internal_items: List[Dict[str, Any]] = []
    placeholder_items: List[Dict[str, Any]] = []

    total_callers = 0
    edge_count = 0
    for node in nodes:
        if not _is_call_candidate(node):
            continue
        callees = _extract_callees(node.code)
        if not callees:
            continue
        total_callers += 1
        for pkg, name in callees:
            if pkg and pkg.upper() in DBMS_SYSTEM_PACKAGES:
                continue
            edge_count += 1
            base = {
                "src_node_type": node.node_type,
                "src_line":      node.start_line,
                "directory":     full_directory,
                "file_name":     file_name,
            }
            if pkg is not None:
                pkg_items.append({**base, "pkg": pkg, "proc_name": name})
            elif name in internal_names:
                internal_items.append({**base, "proc_name": name})
            else:
                placeholder_items.append({**base, "proc_name": name})

    queries: List[Dict[str, Any]] = []
    queries.extend(build_calls_external_pkg_queries(pkg_items))
    queries.extend(build_calls_internal_queries(internal_items))
    queries.extend(build_calls_external_placeholder_queries(placeholder_items))

    logger.info(
        "[CALL_LINKER] AST 결정론 CALLS 쿼리 생성 | callers=%d edges=%d unwind_queries=%d",
        total_callers, edge_count, len(queries),
    )
    return queries


def _is_call_candidate(node: StatementNode) -> bool:
    """CALLS 관계 생성 대상 노드인지 판정."""
    if node.node_type == "CALL":
        return True
    return bool(getattr(node, "initializer_contains_method_call", False))


def _extract_callees(code: str) -> List[Tuple[Optional[str], str]]:
    """code 텍스트에서 callee 이름 리스트 추출.

    Returns:
        [(package|None, function_name), ...] 중복 제거된 리스트
    """
    if not code:
        return []
    # 주석 제거 — 주석 안의 "단어(" 패턴이 callee 로 오인되지 않도록.
    # ANTLR collector 가 prepend 한 `<line>: ` prefix 도 같은 방식으로 처리됨 (한 줄 단위).
    code = _BLOCK_COMMENT_PATTERN.sub("", code)
    code = _LINE_COMMENT_PATTERN.sub("", code)
    found: Set[Tuple[Optional[str], str]] = set()
    # PKG.FUNC 패턴 먼저
    for m in _OBJ_METHOD_PATTERN.finditer(code):
        pkg = m.group(1).strip()
        name = m.group(2).strip()
        if _is_callable_name(name):
            found.add((pkg, name))
    # bare FUNC 패턴 (obj.name 이미 잡힌 건 제외)
    obj_func_names = {name for _, name in found}
    for m in _BARE_METHOD_PATTERN.finditer(code):
        name = m.group(1).strip()
        if name in obj_func_names:
            continue
        if _is_callable_name(name):
            found.add((None, name))
    return list(found)


def _is_callable_name(name: str) -> bool:
    if not name:
        return False
    if name.upper() in _PLSQL_KEYWORDS:
        return False
    # 숫자로 시작하면 식별자 아님
    if name[0].isdigit():
        return False
    return True


def _collect_internal_names(nodes: List[StatementNode]) -> Set[str]:
    """같은 파일 내 PROCEDURE/FUNCTION 이름 수집 (internal 매칭 대상)."""
    names: Set[str] = set()
    for node in nodes:
        if node.node_type in PROCEDURE_TYPES and node.unit_name:
            names.add(node.unit_name.strip())
    return names


