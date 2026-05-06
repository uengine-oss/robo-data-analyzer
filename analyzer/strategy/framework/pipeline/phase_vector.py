"""Framework Phase 4: :Table / :Column 임베딩 벡터라이징.

DBMS Phase 4 와 동일 thin wrapper — 공통 `analyzer.common.pipeline.embedding.vectorize_*` 호출.
Table / Column 벡터라이징은 서로 독립이라 `merge_streams` 로 병렬 실행.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from analyzer.common.pipeline.embedding import vectorize_columns, vectorize_tables
from analyzer.common.pipeline.stream_merge import merge_streams
from client.embedding_client import EmbeddingClient
from config.settings import settings
from util.stream_emitter import emit_user_message

logger = logging.getLogger(__name__)


def _build_embedding_client(api_key: str) -> EmbeddingClient:
    """OpenAI client → EmbeddingClient 래핑.

    `LLM_API_BASE` 가 지정되어 있으면 그 base_url 사용 (자체 임베딩 서버 지원).
    """
    base_url = settings.llm.api_base or None
    openai_client = (
        AsyncOpenAI(api_key=api_key, base_url=base_url)
        if base_url
        else AsyncOpenAI(api_key=api_key)
    )
    return EmbeddingClient(openai_client)


async def run_phase_vector(
    ctx: Any,  # FrameworkPhaseContext
    phase_num: int = 5,
) -> AsyncGenerator[bytes, None]:
    """Framework :Table / :Column 노드 벡터라이징 (embedding=NULL 만).

    Table / Column 벡터라이징은 서로 독립 → 동시 실행. `vectorize_*` 내부에서
    UNWIND + asyncio.gather 로 batch 병렬 + 충돌 자동 재시도.
    """
    api_key = ctx.orchestrator.api_key if hasattr(ctx.orchestrator, "api_key") else None
    if not api_key:
        logger.error("[FRAMEWORK:PHASE:VECTOR] api_key 누락 — pipeline 진입 시점 검증 누락")
        raise RuntimeError("Framework Phase Vector 진입 시 api_key 누락 — service 레이어 검증 필요")

    embedder = _build_embedding_client(api_key)

    async for chunk in merge_streams(
        ("table_vector", vectorize_tables(
            ctx.client, embedder,
            stats=ctx.stats, phase_num=phase_num,
            progress_start=25, progress_end=75,
        )),
        ("column_vector", vectorize_columns(
            ctx.client, embedder,
            stats=ctx.stats,
        )),
    ):
        yield chunk

    yield emit_user_message(
        f"   ✅ 벡터라이징 완료: 테이블 {ctx.stats.tables_vectorized}개, "
        f"컬럼 {ctx.stats.columns_vectorized}개"
    )
