"""상수 전역 컨텍스트 수집.

DBMS/Framework 공통 — Phase 3에서 CONSTANT 노드를 조회하여 LLM 컨텍스트 문자열로 반환.
INIT_BY 관계를 활용하여 상수의 초기화 출처도 포함한다.
"""

from __future__ import annotations

import logging
from typing import Any, List

from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


async def fetch_constants_context(client: Neo4jClient) -> str:
    """Neo4j에서 CONSTANT 노드를 조회하여 LLM 컨텍스트 문자열로 반환.

    Returns:
        "[상수 목록]\nMAX_LOANS: 5\n..." 형식의 문자열. 상수가 없으면 빈 문자열.
    """
    try:
        results = await client.execute_queries([
            "MATCH (c:CONSTANT) RETURN c.name AS name, c.code_text AS code ORDER BY c.name"
        ])
        rows = results[0] if results and results[0] else []
        if not rows:
            return ""
        parts = []
        for r in rows:
            name = r.get("name", "")
            code = (r.get("code") or "").strip()
            if name and code:
                parts.append(code)
            elif name:
                parts.append(name)
        if not parts:
            return ""
        context = "[상수 목록]\n" + "\n".join(parts)
        logger.info("[CONST_CTX] 상수 컨텍스트 생성 | count=%d len=%d", len(parts), len(context))
        return context
    except Exception as e:
        logger.error("[CONST_CTX] 상수 조회 실패 | error=%s", e, exc_info=True)
        return ""


async def fetch_references_with_init_by(client: Neo4jClient, function_id: str) -> str:
    """함수가 참조하는 변수 + 해당 변수의 INIT_BY 출처를 쌍으로 조회한다.

    REFERENCES 관계가 있는 변수에 INIT_BY도 있으면 함께 가져와서
    "변수 ← 상수 초기화" 컨텍스트를 완성한다.

    쿼리 패턴:
      FUNCTION -[:REFERENCES]-> VARIABLE -[:INIT_BY]-> CONSTANT

    Args:
        client: Neo4j 클라이언트.
        function_id: 대상 함수의 function_id.

    Returns:
        "[변수 참조 + 초기화 출처]
         limit (VARIABLE) ← MAX_LOANS (CONSTANT)
         ..." 형식의 문자열. 참조가 없으면 빈 문자열.
    """
    query = (
        "MATCH (f:FUNCTION {function_id: $fid})-[:REFERENCES]->(v) "
        "WHERE v:VARIABLE OR v:CONSTANT "
        "OPTIONAL MATCH (v)-[:INIT_BY]->(src) "
        "RETURN v.name AS var_name, v.variable_id AS var_id, "
        "  labels(v) AS var_labels, v.code_text AS var_code, "
        "  src.name AS src_name, src.variable_id AS src_id, "
        "  labels(src) AS src_labels, src.code_text AS src_code "
        "ORDER BY v.name"
    )
    try:
        results = await client.execute_queries([{"query": query, "params": {"fid": function_id}}])
        rows = results[0] if results and results[0] else []
        if not rows:
            return ""

        parts = []
        for r in rows:
            var_name = r.get("var_name", "")
            var_labels = r.get("var_labels", [])
            var_label = "CONSTANT" if "CONSTANT" in var_labels else "VARIABLE"
            src_name = r.get("src_name")
            src_labels = r.get("src_labels") or []
            src_label = "CONSTANT" if "CONSTANT" in src_labels else "VARIABLE"

            if src_name:
                # INIT_BY 관계 있음: 쌍으로 표시
                parts.append(f"{var_name} ({var_label}) ← {src_name} ({src_label})")
            else:
                # INIT_BY 없음: 변수만 표시
                parts.append(f"{var_name} ({var_label})")

        if not parts:
            return ""

        context = "[변수 참조 + 초기화 출처]\n" + "\n".join(parts)
        logger.info("[CONST_CTX] 참조+INIT_BY 컨텍스트 | function=%s refs=%d", function_id, len(parts))
        return context
    except Exception as e:
        logger.error("[CONST_CTX] 참조+INIT_BY 조회 실패 | function=%s error=%s", function_id, e, exc_info=True)
        return ""
