"""상수 전역 컨텍스트 수집.

DBMS/Framework 공통 — Phase 3에서 CONSTANT 노드를 조회하여 LLM 컨텍스트 문자열로 반환.
"""

from __future__ import annotations

import logging

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
