"""DBMS Phase 4: :Table / :Column 임베딩 벡터라이징.

OpenAI client 초기화 + analyzer/common/pipeline/embedding 의 strategy-agnostic 빌더 호출.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from analyzer.common.pipeline.embedding import vectorize_columns, vectorize_tables
from client.embedding_client import EmbeddingClient
from config.settings import settings
from util.stream_emitter import emit_message

logger = logging.getLogger(__name__)


def _build_embedding_client(api_key: str) -> EmbeddingClient:
    """OpenAI client → EmbeddingClient 래핑.

    LLM_API_BASE 가 지정되어 있으면 그 base_url 사용 (자체 임베딩 서버 지원).
    """
    base_url = settings.llm.api_base or None
    openai_client = (
        AsyncOpenAI(api_key=api_key, base_url=base_url)
        if base_url
        else AsyncOpenAI(api_key=api_key)
    )
    return EmbeddingClient(openai_client)


async def run_phase_vector(
    ctx: Any,  # DbmsPhaseContext
    phase_num: int = 4,
) -> AsyncGenerator[bytes, None]:
    """:Table 와 :Column 노드 벡터라이징 (embedding=NULL 만)."""
    if not ctx.api_key:
        logger.error("[PHASE:4:VECTOR] api_key 누락 — pipeline 진입 시점 검증 누락")
        raise RuntimeError("Phase4(VECTOR) 진입 시 api_key 누락 — service 레이어 검증 필요")

    embedder = _build_embedding_client(ctx.api_key)

    # Table 벡터라이징 — 진행률 emit (25% → 75%) + stats.tables_vectorized 누적
    async for evt in vectorize_tables(
        ctx.client, embedder,
        stats=ctx.stats,
        batch_size=32, phase_num=phase_num,
        progress_start=25, progress_end=75,
    ):
        yield evt

    # Column 벡터라이징 — 카운트 반환 + stats 누적
    column_count = await vectorize_columns(ctx.client, embedder, batch_size=32)
    ctx.stats.columns_vectorized += column_count

    yield emit_message(
        f"   ✅ 벡터라이징 완료: 테이블 {ctx.stats.tables_vectorized}개, 컬럼 {column_count}개"
    )
