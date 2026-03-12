"""Neo4j 배치 실행 유틸리티."""
from __future__ import annotations
import logging
from typing import Any, AsyncGenerator, Dict, List
from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


async def run_graph_queries(
    client: Neo4jClient,
    queries: List[Dict[str, Any]],
    label: str = "",
) -> AsyncGenerator[Dict[str, list], None]:
    """쿼리 리스트를 Neo4j에 배치 단위로 실행하고, 배치마다 그래프 결과를 yield.

    DBMS 전략과 동일하게 client.run_graph_query()의 배치 단위 yield를
    그대로 프론트엔드까지 전달하여 점진적 그래프 업데이트를 지원합니다.

    Yields:
        {"Nodes": [...], "Relationships": [...]}  (배치 단위)
    """
    if not queries:
        return
    try:
        total_yielded = 0
        async for batch_result in client.run_graph_query(queries):
            nodes = batch_result.get("Nodes", [])
            rels = batch_result.get("Relationships", [])
            if nodes or rels:
                total_yielded += len(nodes) + len(rels)
                yield {"Nodes": nodes, "Relationships": rels}
        logger.info(
            "[FW:GRAPH] Neo4j 반영 완료 | %s queries=%d items=%d",
            label, len(queries), total_yielded,
        )
    except Exception as e:
        logger.warning("[FW:GRAPH] Neo4j 반영 실패 | %s error=%s", label, e)
