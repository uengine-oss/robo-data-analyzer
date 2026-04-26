"""DBMS 결정론 REFERENCES 관계 빌더 (Phase 2-b).

AST 의 변수 선언 노드 수집 + 분석 대상 노드 code 에서 변수명 토큰 매칭.
LLM 미사용.

두 종류 변수:
  1) DECLARE 안 VARIABLE (local scope)
     - scope = 가장 가까운 PROCEDURE/FUNCTION
     - antlr-parser Step 0-2 에서 DECLARE 의 child 로 생성됨
  2) PACKAGE_VARIABLE / CONSTANT_FIELD (package scope)
     - scope = 파일 전체
     - 기존 antlr-parser 의 enterPackage_obj_spec 이 생성
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from analyzer.strategy.dbms.common.domain.analysis_constants import PROCEDURE_TYPES
from analyzer.strategy.dbms.common.cypher.graph_queries import build_references_query
from analyzer.strategy.dbms.common.models.node_models import StatementNode

logger = logging.getLogger(__name__)

_PACKAGE_VAR_TYPES: Set[str] = {"PACKAGE_VARIABLE", "CONSTANT_FIELD"}


def build_references_queries(
    nodes: List[StatementNode],
    full_directory: str,
    file_name: str,
) -> List[Dict[str, Any]]:
    """REFERENCES 관계 쿼리 리스트 생성.

    Returns:
        Neo4j 쿼리 dict 리스트
    """
    if not nodes:
        return []

    local_vars, pkg_vars = _collect_variables(nodes)
    if not local_vars and not pkg_vars:
        return []

    queries: List[Dict[str, Any]] = []
    # 각 분석 대상 노드의 code 에서 변수명 토큰 매칭
    for n in nodes:
        if not n.analyzable or not n.code:
            continue
        # Package 변수: 같은 파일 전체 scope
        for v in pkg_vars:
            if _token_in_code(v["name"], n.code):
                queries.append(_build_ref_query(n, v, full_directory, file_name))
        # Local 변수: 같은 프로시저 scope 안에 있는 노드만
        for v in local_vars:
            if not _is_inside_scope(n, v["scope_node"]):
                continue
            if _token_in_code(v["name"], n.code):
                queries.append(_build_ref_query(n, v, full_directory, file_name))

    logger.info(
        "[VAR_MATCHER] AST 결정론 REFERENCES 쿼리 생성 | local=%d pkg=%d edges=%d",
        len(local_vars), len(pkg_vars), len(queries),
    )
    return queries


def _collect_variables(
    nodes: List[StatementNode],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """VARIABLE (local) + PACKAGE_VARIABLE/CONSTANT_FIELD (pkg) 수집."""
    local_vars: List[Dict[str, Any]] = []
    pkg_vars: List[Dict[str, Any]] = []
    for n in nodes:
        if not n.node_type:
            continue
        name = n.name.strip()
        if not name:
            continue
        if n.node_type == "VARIABLE":
            scope_node = _find_enclosing_procedure(n)
            local_vars.append({
                "name": name,
                "start_line": n.start_line,
                "scope_node": scope_node,
                "kind": "local",
            })
        elif n.node_type in _PACKAGE_VAR_TYPES:
            pkg_vars.append({
                "name": name,
                "start_line": n.start_line,
                "kind": "package",
            })
    return local_vars, pkg_vars


def _find_enclosing_procedure(node: StatementNode) -> Optional[StatementNode]:
    """가장 가까운 PROCEDURE/FUNCTION/TRIGGER 조상 반환."""
    current = node.parent
    while current is not None:
        if current.node_type in PROCEDURE_TYPES:
            return current
        current = current.parent
    return None


def _is_inside_scope(node: StatementNode, scope_node: Optional[StatementNode]) -> bool:
    """node 가 scope_node 의 자손인지."""
    if scope_node is None:
        return True  # scope 불명 시 허용 (conservative)
    current = node
    while current is not None:
        if current is scope_node:
            return True
        current = current.parent
    return False


def _token_in_code(var_name: str, code: str) -> bool:
    """변수명이 code 에 단어 경계로 등장하는지 (대소문자 구분)."""
    pattern = r"\b" + re.escape(var_name) + r"\b"
    return bool(re.search(pattern, code))


def _build_ref_query(
    node: StatementNode,
    var: Dict[str, Any],
    full_directory: str,
    file_name: str,
) -> Dict[str, Any]:
    """단일 REFERENCES 쿼리 생성. graph_queries 빌더 위임."""
    return build_references_query(
        src_node_type=node.node_type, src_line=node.start_line,
        var_name=var["name"],
        full_directory=full_directory, file_name=file_name,
    )
