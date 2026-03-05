"""exp 전용 Neo4j 배치 기록. 기존 client 미참조, 환경변수만 사용."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# 배치 크기: .env의 NEO4J_QUERY_BATCH_SIZE 사용 (config 로드로 .env 이미 적용됨)
DEFAULT_BATCH_SIZE = int(os.environ.get("NEO4J_QUERY_BATCH_SIZE", "30"))


def _create_driver():
    """neo4j 드라이버 생성. .env의 NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD 사용."""
    from neo4j import AsyncGraphDatabase
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "neo4j")
    return AsyncGraphDatabase.driver(uri, auth=(user, password))


class ExpNeo4jWriter:
    """exp 전용 Neo4j 배치 기록. run_graph_query처럼 쿼리 리스트를 배치로 실행."""

    def __init__(self, database: Optional[str] = None, batch_size: int = DEFAULT_BATCH_SIZE):
        self._driver = None
        self._database = database or os.environ.get("NEO4J_DATABASE", "neo4j")
        self._batch_size = batch_size

    async def connect(self) -> None:
        if self._driver is None:
            self._driver = _create_driver()

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

    async def run_queries_batch(
        self,
        queries: List[Union[str, Dict[str, Any]]],
        batch_size: Optional[int] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """쿼리를 배치 단위로 실행하고 각 배치 결과를 yield (기존 run_graph_query와 동일 패턴)."""
        if not queries:
            logger.info("[EXP:NEO4J] 배치 스킵 | reason=empty_queries")
            yield {"Nodes": [], "Relationships": [], "batch": 0, "total_batches": 0}
            return
        size = batch_size if batch_size is not None else self._batch_size
        total_batches = (len(queries) + size - 1) // size
        await self.connect()
        try:
            async with self._driver.session(database=self._database) as session:
                for batch_idx in range(total_batches):
                    start = batch_idx * size
                    end = min(start + size, len(queries))
                    batch_queries = queries[start:end]
                    nodes: Dict[str, dict] = {}
                    relationships: Dict[str, dict] = {}
                    for query in batch_queries:
                        if isinstance(query, dict):
                            query_str = query.get("query", "").strip()
                            params = query.get("params") or query.get("parameters") or {}
                        else:
                            query_str = str(query).strip()
                            params = {}
                        if not query_str:
                            logger.info("[EXP:NEO4J] 쿼리 스킵 | reason=empty_query_string")
                            continue
                        try:
                            result = await session.run(query_str, params)
                            graph = await result.graph()
                        except Exception as e:
                            logger.warning("Neo4j 쿼리 실행 실패: %s", e)
                            continue
                        for node in graph.nodes:
                            eid = node.element_id
                            if eid not in nodes:
                                nodes[eid] = {
                                    "Node ID": eid,
                                    "Labels": list(node.labels),
                                    "Properties": dict(node),
                                }
                        for rel in graph.relationships:
                            rid = rel.element_id
                            if rid not in relationships:
                                relationships[rid] = {
                                    "Relationship ID": rid,
                                    "Type": rel.type,
                                    "Properties": dict(rel),
                                    "Start Node ID": rel.start_node.element_id,
                                    "End Node ID": rel.end_node.element_id,
                                }
                    logger.info(
                        "[EXP:NEO4J] 배치 실행 | batch=%d/%d queries=%d",
                        batch_idx + 1, total_batches, len(batch_queries),
                    )
                    yield {
                        "Nodes": list(nodes.values()),
                        "Relationships": list(relationships.values()),
                        "batch": batch_idx + 1,
                        "total_batches": total_batches,
                    }
        except Exception as e:
            logger.exception("run_queries_batch 오류")
            raise RuntimeError(f"Neo4j 배치 실행 실패: {e}") from e

    async def run_queries_batch_sync(
        self,
        queries: List[Union[str, Dict[str, Any]]],
        batch_size: Optional[int] = None,
    ) -> None:
        """배치로 실행만 하고 결과는 버림. 호출마다 같은 루프에서 연결·실행·종료 (여러 asyncio.run()에서 재사용 시 루프 불일치 방지)."""
        try:
            async for _ in self.run_queries_batch(queries, batch_size):
                pass
        finally:
            await self.close()

    async def fetch_calls_edges(self) -> List[Dict[str, Any]]:
        """Neo4j에서 (METHOD)-[:CALLS]->(METHOD) 조회. Step6 호출 그래프 로드용."""
        query = (
            "MATCH (a:METHOD)-[:CALLS]->(b:METHOD) "
            "RETURN a.method_id AS caller_method_id, b.method_id AS callee_method_id"
        )
        await self.connect()
        try:
            async with self._driver.session(database=self._database) as session:
                result = await session.run(query, {})
                records = await result.data()
                return list(records) if records else []
        finally:
            await self.close()

    async def count_calls_and_uses(self) -> tuple[int, int]:
        """Neo4j에 저장된 CALLS, USES 관계 개수 반환. flush 검증용."""
        await self.connect()
        try:
            async with self._driver.session(database=self._database) as session:
                r_calls = await session.run("MATCH ()-[r:CALLS]->() RETURN count(r) AS c", {})
                calls_count = (await r_calls.single())["c"] or 0
                r_uses = await session.run("MATCH ()-[r:USES]->() RETURN count(r) AS c", {})
                uses_count = (await r_uses.single())["c"] or 0
                return int(calls_count), int(uses_count)
        finally:
            await self.close()
