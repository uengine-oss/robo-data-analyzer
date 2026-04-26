"""Embedding client for text vectorization."""
from typing import List

from openai import AsyncOpenAI

from config.settings import settings


class EmbeddingClient:
    """Client for generating text embeddings"""

    def __init__(self, client: AsyncOpenAI):
        self.client = client
        self.model = settings.llm.embedding_model

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text. 빈 텍스트는 fail-fast."""
        if not text or not text.strip():
            raise ValueError("EmbeddingClient.embed_text: 빈 텍스트는 임베딩 입력으로 허용 안 됨 — 호출자가 검증할 것")
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
            encoding_format="float"
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts. 빈 텍스트가 섞여 있으면 fail-fast."""
        if not texts:
            return []

        empty_indices = [i for i, t in enumerate(texts) if not t or not t.strip()]
        if empty_indices:
            raise ValueError(
                f"EmbeddingClient.embed_batch: 빈 텍스트 항목 발견 (indices={empty_indices[:5]}...) "
                "— 호출자가 build_table_text/build_column_text 단계에서 검증할 것"
            )

        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
            encoding_format="float"
        )
        return [item.embedding for item in response.data]

