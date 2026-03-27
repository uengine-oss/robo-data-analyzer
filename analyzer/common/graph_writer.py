"""Neo4j 배치 실행 유틸리티

DBMS, Framework 양쪽에서 사용하는 공통 그래프 쿼리 실행 래퍼.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from client.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


async def run_graph_queries(
    client: Neo4jClient,
    queries: List[Dict[str, Any]],
    *,
    label: str = "",
    cypher_lock: Optional[asyncio.Lock] = None,
) -> AsyncGenerator[Dict[str, list], None]:
    """쿼리 리스트를 Neo4j에 배치 단위로 실행하고, 배치마다 그래프 결과를 yield.

    Args:
        client: Neo4j 클라이언트
        queries: 실행할 쿼리 리스트
        label: 로깅용 라벨
        cypher_lock: 동시성 제어용 락 (None이면 락 없이 실행)

    Yields:
        {"Nodes": [...], "Relationships": [...]}  (배치 단위)
    """
    if not queries:
        return

    async def _execute():
        total_yielded = 0
        async for batch_result in client.run_graph_query(queries):
            nodes = batch_result.get("Nodes", [])
            rels = batch_result.get("Relationships", [])
            if nodes or rels:
                total_yielded += len(nodes) + len(rels)
                yield {"Nodes": nodes, "Relationships": rels}
        if label:
            logger.info(
                "[GRAPH] Neo4j 반영 완료 | %s queries=%d items=%d",
                label, len(queries), total_yielded,
            )

    try:
        if cypher_lock:
            async with cypher_lock:
                async for result in _execute():
                    yield result
        else:
            async for result in _execute():
                yield result
    except Exception as e:
        logger.warning("[GRAPH] Neo4j 반영 실패 | %s error=%s", label, e)


async def run_graph_queries_collect(
    client: Neo4jClient,
    queries: List[Dict[str, Any]],
    *,
    cypher_lock: Optional[asyncio.Lock] = None,
) -> Dict[str, list]:
    """쿼리를 실행하고 결과를 합산하여 반환 (DBMS 패턴).

    Returns:
        {"Nodes": [...], "Relationships": [...]}
    """
    all_nodes: dict = {}
    all_rels: dict = {}

    async for batch in run_graph_queries(client, queries, cypher_lock=cypher_lock):
        for node in batch.get("Nodes", []):
            all_nodes[node["Node ID"]] = node
        for rel in batch.get("Relationships", []):
            all_rels[rel["Relationship ID"]] = rel

    return {
        "Nodes": list(all_nodes.values()),
        "Relationships": list(all_rels.values()),
    }
