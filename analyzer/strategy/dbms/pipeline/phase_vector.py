"""DBMS Phase 4: 벡터라이징 — enrichment/table_description_enrichment 호출 wrapper (벡터라이징)."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from analyzer.strategy.dbms.enrichment.table_description_enrichment import vectorize_dbms_table_column


async def run_phase_vector(
    ctx: Any,  # DbmsPhaseContext
    phase_num: int = 4,
) -> AsyncGenerator[bytes, None]:
    """:Table / :Column 노드 임베딩 생성 + Neo4j 저장."""
    async for chunk in vectorize_dbms_table_column(
        client=ctx.client,
        api_key=ctx.api_key,
        stats=ctx.stats,
        phase_num=phase_num,
    ):
        yield chunk
