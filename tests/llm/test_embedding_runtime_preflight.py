from types import SimpleNamespace
from unittest.mock import patch

import pytest

from llm import embedder


def _settings(provider: str, model: str) -> SimpleNamespace:
    return SimpleNamespace(embedding=SimpleNamespace(
        provider=provider,
        model=model,
    ))


def test_sglang_cannot_fall_through_to_openai_endpoint() -> None:
    with (
        patch.object(embedder, "SETTINGS", _settings("sglang", "BAAI/bge-m3")),
        patch.object(
            embedder,
            "_resolve_endpoint",
            return_value=("https://api.openai.com/v1/embeddings", "secret"),
        ),
    ):
        with pytest.raises(embedder.EmbeddingError, match="sglang.*OpenAI"):
            embedder.validate_runtime_configuration()


def test_openai_requires_embedding_model_before_paid_generation() -> None:
    with (
        patch.object(embedder, "SETTINGS", _settings("openai", "BAAI/bge-m3")),
        patch.object(
            embedder,
            "_resolve_endpoint",
            return_value=("https://api.openai.com/v1/embeddings", "secret"),
        ),
    ):
        with pytest.raises(embedder.EmbeddingError, match="text-embedding"):
            embedder.validate_runtime_configuration()


def test_openai_embedding_tuple_passes() -> None:
    with (
        patch.object(
            embedder,
            "SETTINGS",
            _settings("openai", "text-embedding-3-small"),
        ),
        patch.object(
            embedder,
            "_resolve_endpoint",
            return_value=("https://api.openai.com/v1/embeddings", "secret"),
        ),
    ):
        embedder.validate_runtime_configuration()
