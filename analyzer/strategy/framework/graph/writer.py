"""Neo4j 배치 실행 유틸리티."""
from __future__ import annotations
import logging
from typing import Any, Dict, List
from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

async def run_graph_queries(client: Neo4jClient, queries: List[Dict[str, Any]], label: str = "") -> int:
    """쿼리 리스트를 Neo4j에 일괄 실행. 반환: 실행된 쿼리 수."""
    if not queries:
        return 0
    try:
        async for _ in client.run_graph_query(queries):
            pass
        logger.info("[FW:GRAPH] Neo4j 반영 완료 | %s queries=%d", label, len(queries))
        return len(queries)
    except Exception as e:
        logger.warning("[FW:GRAPH] Neo4j 반영 실패 | %s error=%s", label, e)
        return 0
