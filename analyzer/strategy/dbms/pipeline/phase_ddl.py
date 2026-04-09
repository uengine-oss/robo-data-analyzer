"""DBMS Phase 0: DDL 처리.

공통 DDL 처리 로직을 analyzer/common/ddl_phase.py에서 호출.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from analyzer.common.ddl_phase import run_ddl_processing
from util.ndjson_stream import (
    emit_message, emit_separator, emit_phase_header, emit_file_start,
)


async def run_phase_ddl(
    ctx: Any,  # DbmsPhaseContext
) -> AsyncGenerator[bytes, None]:
    """Phase 0: DDL 처리 (DBMS)."""
    yield emit_message("")
    yield emit_separator()
    yield emit_phase_header(0, "📋 DDL 스키마 수집")
    yield emit_separator()

    async for chunk in run_ddl_processing(
        orchestrator=ctx.orchestrator,
        client=ctx.client,
        cypher_lock=ctx.cypher_lock,
        ddl_schemas=ctx.ddl_schemas,
        ddl_table_metadata=ctx.ddl_table_metadata,
        on_file_start=lambda idx, total, fname: emit_file_start(idx, total, fname),
    ):
        yield chunk

    yield emit_message("")
    yield emit_message("📊 DDL 처리 완료:")
    yield emit_message(f"   • 테이블: {ctx.stats.ddl_tables}개")
    yield emit_message(f"   • 컬럼: {ctx.stats.ddl_columns}개")
    yield emit_message(f"   • FK: {ctx.stats.ddl_fks}개")
