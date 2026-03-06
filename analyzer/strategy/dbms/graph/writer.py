"""Neo4j 배치 실행 래퍼

공통 cypher_lock + client.run_graph_query 패턴 추출.
"""

import asyncio
from typing import Any, Dict, List

from client.neo4j_client import Neo4jClient


async def run_graph_queries(
    queries: List[Dict[str, Any]],
    client: Neo4jClient,
    cypher_lock: asyncio.Lock,
) -> Dict[str, list]:
    """Cypher 쿼리를 배치 실행하고 결과를 병합합니다.

    Args:
        queries: 실행할 쿼리 리스트
        client: Neo4j 클라이언트
        cypher_lock: Cypher 실행 동기화 락

    Returns:
        {"Nodes": [...], "Relationships": [...]}
    """
    all_nodes: dict = {}
    all_relationships: dict = {}

    if not queries:
        return {"Nodes": [], "Relationships": []}

    async with cypher_lock:
        async for batch_result in client.run_graph_query(queries):
            for node in batch_result.get("Nodes", []):
                all_nodes[node["Node ID"]] = node
            for rel in batch_result.get("Relationships", []):
                all_relationships[rel["Relationship ID"]] = rel

    return {
        "Nodes": list(all_nodes.values()),
        "Relationships": list(all_relationships.values()),
    }
