"""Phase 진행률 관리 + 공용 Neo4j write 헬퍼.

모든 Phase에서 동일한 패턴으로 진행률 보고 + Neo4j 쓰기를 수행.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from client.neo4j_client import Neo4jClient
from util.ndjson_stream import emit_data, PhaseTracker
from analyzer.common.graph_writer import run_graph_queries

logger = logging.getLogger(__name__)


async def write_to_graph(
    client: Neo4jClient,
    queries: List[Dict[str, Any]],
    label: str,
) -> AsyncGenerator[bytes, None]:
    """Neo4j 쿼리 실행 + emit_data 스트리밍.

    실패 시 logger.error + raise (실패를 숨기지 않음).
    모든 Phase에서 이 함수를 통해 Neo4j에 쓴다.
    """
    if not queries:
        return
    async for graph in run_graph_queries(client, queries, label=label):
        yield emit_data(graph=graph)
