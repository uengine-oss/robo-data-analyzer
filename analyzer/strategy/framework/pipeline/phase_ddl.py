"""Phase 0: DDL 스키마 수집 (선택적).

공통 DDL 처리 로직을 analyzer/common/ddl_phase.py에서 호출.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.common.ddl_handler import run_ddl_processing
from util.stream_emitter import emit_message


async def run_phase_ddl(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 0: DDL 파일 처리 (Framework)."""
    ddl_schemas: set[str] = set()
    ddl_table_metadata: dict = {}

    async for chunk in run_ddl_processing(
        orchestrator=orchestrator,
        client=client,
        cypher_lock=pipeline._cypher_lock,
        ddl_schemas=ddl_schemas,
        ddl_table_metadata=ddl_table_metadata,
    ):
        yield chunk

    pipeline._pipeline_state.ddl_schemas = ddl_schemas
    pipeline._pipeline_state.ddl_table_metadata = ddl_table_metadata
