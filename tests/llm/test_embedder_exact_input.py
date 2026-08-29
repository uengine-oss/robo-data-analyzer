from unittest.mock import patch

from llm import embedder


def test_long_search_text_reaches_embedding_provider_without_truncation() -> None:
    source = "업무 의미 " + "가" * 5000 + " 마지막 의미"
    received: list[list[str]] = []

    def encode(texts: list[str]) -> list[list[float]]:
        received.append(list(texts))
        return [[0.25, 0.75] for _text in texts]

    provider = embedder.SETTINGS.embedding.provider
    with patch.dict(embedder._ENCODERS, {provider: encode}):
        vectors = embedder.encode_texts([source])

    assert received == [[source]]
    assert vectors == [[0.25, 0.75]]
