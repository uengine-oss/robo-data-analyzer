"""DBMS Phase 5: 리니지 분석 — enrichment/data_lineage 호출 wrapper."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from analyzer.common.pipeline.data_lineage import run_data_lineage
from config.settings import settings


async def run_phase_lineage(
    ctx: Any,  # DbmsPhaseContext
    phase_num: int = 5,
) -> AsyncGenerator[bytes, None]:
    """source 디렉토리의 .sql 파일 → catalog 리니지 분석 위임."""
    source_dir = ctx.orchestrator.dirs.get("source", "")
    async for chunk in run_data_lineage(
        source_dir=source_dir,
        dbms=ctx.target,
        name_case=ctx.name_case,
        catalog_url=settings.catalog.service_url,
        stats=ctx.stats,
        phase_num=phase_num,
    ):
        yield chunk
