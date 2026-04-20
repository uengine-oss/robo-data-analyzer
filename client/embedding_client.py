"""Embedding client for text vectorization
Ported from neo4j-text2sql/app/core/embedding.py
"""
from typing import List
from openai import AsyncOpenAI

from config.settings import settings


class EmbeddingClient:
    """Client for generating text embeddings"""

    def __init__(self, client: AsyncOpenAI):
        self.client = client
        self.model = settings.llm.embedding_model

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text"""
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
            encoding_format="float"
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts"""
        if not texts:
            return []

        # Filter out empty texts
        valid_texts = [t if t.strip() else "empty" for t in texts]

        response = await self.client.embeddings.create(
            model=self.model,
            input=valid_texts,
            encoding_format="float"
        )
        return [item.embedding for item in response.data]

