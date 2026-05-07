"""DBMS Phase 0: DDL 처리.

공통 DDL 처리 로직을 analyzer/common/ddl_phase.py에서 호출.
DDL 적재 직후 Phase 2B (dml_extract LLM) 가 사용할 ddl_table_index + ddl_pattern 도 빌드.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from analyzer.common.cypher.ddl_handler import run_ddl_processing
from analyzer.strategy.dbms.linking import ddl_table_matcher
from util.stream_emitter import (
    emit_user_message, emit_separator, emit_phase_header, emit_file_start,
)


async def run_phase_ddl(
    ctx: Any,  # DbmsPhaseContext
) -> AsyncGenerator[bytes, None]:
    """Phase 0: DDL 처리 (DBMS).

    1. DDL 파일 → Neo4j Table/Column 노드 적재 + ddl_table_metadata 빌드 (in-place)
    2. ddl_table_metadata → ddl_table_index 변환 (테이블명 키 + 컬럼 list)
    3. ddl_pattern 컴파일 (Phase 2B per range 매칭용)
    """
    yield emit_user_message("")
    yield emit_separator()
    yield emit_phase_header(0, "📋 DDL 스키마 수집")
    yield emit_separator()

    async for chunk in run_ddl_processing(
        orchestrator=ctx.orchestrator,
        client=ctx.client,
        ddl_schemas=ctx.ddl_schemas,
        ddl_table_metadata=ctx.ddl_table_metadata,
        on_file_start=lambda idx, total, fname: emit_file_start(idx, total, fname),
    ):
        yield chunk

    # ddl_table_metadata → ddl_table_index 변환 (Phase 2B dml_extract 가 사용하는 형식)
    ddl_table_index = {}
    for (_schema_lc, _name_lc), meta in ctx.ddl_table_metadata.items():
        original_name = meta.get("original_name", "")
        if not original_name:
            continue
        ddl_table_index[original_name.upper()] = {
            "schema": meta.get("original_schema", ""),
            "description": meta.get("description", "") or "",
            "columns": [
                {
                    "name": col_name,
                    "description": (col_meta.get("description") or "") if isinstance(col_meta, dict) else "",
                    "dtype": (col_meta.get("dtype") or "") if isinstance(col_meta, dict) else "",
                    "nullable": col_meta.get("nullable", True) if isinstance(col_meta, dict) else True,
                }
                for col_name, col_meta in (meta.get("columns") or {}).items()
            ],
        }
    ctx.state.ddl_table_index = ddl_table_index
    ctx.state.ddl_pattern = ddl_table_matcher.build_pattern(list(ddl_table_index.keys()))

    yield emit_user_message("")
    yield emit_user_message("📊 DDL 처리 완료:")
    yield emit_user_message(f"   • 테이블: {ctx.stats.ddl_tables}개")
    yield emit_user_message(f"   • 컬럼: {ctx.stats.ddl_columns}개")
    yield emit_user_message(f"   • FK: {ctx.stats.ddl_fks}개")
    yield emit_user_message(f"   • ddl_table_index: {len(ddl_table_index)}개 테이블, 단어 매칭 패턴 {'활성' if ctx.state.ddl_pattern else '비활성'}")
