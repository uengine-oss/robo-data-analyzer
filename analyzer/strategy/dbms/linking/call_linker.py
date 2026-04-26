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
    build_calls_external_pkg_query,
    build_calls_external_placeholder_query,
    build_calls_internal_query,
)
from analyzer.strategy.dbms.common.models.node_models import StatementNode

logger = logging.getLogger(__name__)


# ── callee 추출 regex (Framework call_linker 와 동일 패턴) ─────────────────
# PKG.FUNC( — 스키마/패키지 접두사 있는 호출
_OBJ_METHOD_PATTERN = re.compile(r"\b(\w+)\s*\.\s*(\w+)\s*\(")
# := FUNC( 또는 일반 FUNC( — 단독 호출
_BARE_METHOD_PATTERN = re.compile(r"(?:^|:=|[\s(,])(\w+)\s*\(")

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
    """CALLS 관계 쿼리 리스트 생성.

    Args:
        nodes: Phase 1 에서 수집된 StatementNode 리스트
        full_directory: 파일 full path (디렉토리/파일명)
        file_name: 파일명

    Returns:
        Neo4j 쿼리 dict 리스트
    """
    if not nodes:
        return []

    # 같은 파일 내 PROCEDURE/FUNCTION 이름 수집 (internal 판정용)
    internal_names = _collect_internal_names(nodes)

    queries: List[Dict[str, Any]] = []
    total_callers = 0
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
            queries.append(
                _build_calls_query(node, pkg, name, internal_names, full_directory, file_name)
            )

    logger.info(
        "[CALL_LINKER] AST 결정론 CALLS 쿼리 생성 | callers=%d edges=%d",
        total_callers, len(queries),
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


def _build_calls_query(
    src_node: StatementNode,
    pkg: Optional[str],
    name: str,
    internal_names: Set[str],
    full_directory: str,
    file_name: str,
) -> Dict[str, Any]:
    """단일 CALLS 쿼리 생성. graph_queries 빌더에 위임.

    PKG.FUNC 형식은 항상 external (외부 패키지).
    bare FUNC 는 internal_names 에 있으면 internal, 없으면 external placeholder.
    """
    if pkg is not None:
        return build_calls_external_pkg_query(
            src_node_type=src_node.node_type, src_line=src_node.start_line,
            pkg=pkg, proc_name=name,
            full_directory=full_directory, file_name=file_name,
        )
    if name in internal_names:
        return build_calls_internal_query(
            src_node_type=src_node.node_type, src_line=src_node.start_line,
            proc_name=name,
            full_directory=full_directory, file_name=file_name,
        )
    return build_calls_external_placeholder_query(
        src_node_type=src_node.node_type, src_line=src_node.start_line,
        proc_name=name,
        full_directory=full_directory, file_name=file_name,
    )
