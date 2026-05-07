"""Phase 0: DDL 스키마 수집 (선택적).

공통 DDL 처리 로직을 analyzer/common/cypher/ddl_handler.py에서 호출.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from analyzer.common.cypher.ddl_handler import run_ddl_processing


async def run_phase_ddl(
    ctx: Any,  # FrameworkPhaseContext
) -> AsyncGenerator[bytes, None]:
    """Phase 0: DDL 파일 처리 (Framework)."""
    ddl_schemas: set[str] = set()
    ddl_table_metadata: dict = {}

    async for chunk in run_ddl_processing(
        orchestrator=ctx.orchestrator,
        client=ctx.client,
        ddl_schemas=ddl_schemas,
        ddl_table_metadata=ddl_table_metadata,
    ):
        yield chunk

    ctx.state.ddl_schemas = ddl_schemas
    ctx.state.ddl_table_metadata = ddl_table_metadata
