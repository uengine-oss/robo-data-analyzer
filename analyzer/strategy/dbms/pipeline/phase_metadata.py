"""DBMS Phase 3: 메타데이터 보강 — enrichment/metadata_enrichment 호출 wrapper."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from analyzer.strategy.dbms.enrichment.metadata_enrichment import run_metadata_enrichment
from config.settings import settings


async def run_phase_metadata(
    ctx: Any,  # DbmsPhaseContext
    phase_num: int = 3,
) -> AsyncGenerator[bytes, None]:
    """catalog 서비스에 메타데이터 보강 위임."""
    async for chunk in run_metadata_enrichment(
        datasource_name=ctx.datasource_name,
        api_key=ctx.api_key,
        catalog_url=settings.catalog.service_url,
        phase_num=phase_num,
    ):
        yield chunk
