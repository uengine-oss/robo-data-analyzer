import asyncio
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import httpx

from llm import text_embedder
from llm.usage.cost_budget import CostBudget


def test_long_search_text_reaches_embedding_provider_without_truncation() -> None:
    source = "업무 의미 " + "가" * 5000 + " 마지막 의미"
    received: list[list[str]] = []

    def encode(texts: list[str]) -> list[list[float]]:
        received.append(list(texts))
        return [[0.25, 0.75] for _text in texts]

    provider = text_embedder.SETTINGS.embedding.provider
    with patch.dict(text_embedder._ENCODERS, {provider: encode}):
        vectors = text_embedder.encode_texts([source])

    assert received == [[source]]
    assert vectors == [[0.25, 0.75]]


class _EmbeddingResponse:
    def __init__(self, *, usage: dict | None) -> None:
        self._usage = usage

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        payload = {
            "model": "text-embedding-3-small",
            "data": [{"embedding": [0.25, 0.75]}],
        }
        if self._usage is not None:
            payload["usage"] = self._usage
        return payload


class _EmbeddingClient:
    def __init__(self, response: _EmbeddingResponse) -> None:
        self.response = response

    async def post(self, _url: str, *, json: dict) -> _EmbeddingResponse:
        assert json == {
            "model": text_embedder.SETTINGS.embedding.model,
            "input": ["업무 의미"],
        }
        return self.response


class _RetryableFailureClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, url: str, *, json: dict) -> httpx.Response:
        self.calls += 1
        request = httpx.Request("POST", url, json=json)
        return httpx.Response(429, request=request)


def test_paid_embedding_usage_is_persisted_and_charged() -> None:
    response = _EmbeddingResponse(usage={"prompt_tokens": 7, "total_tokens": 7})
    budget = CostBudget(limit_usd=1.0)
    with tempfile.TemporaryDirectory() as temporary:
        usage_path = Path(temporary) / "usage.jsonl"
        vectors = asyncio.run(text_embedder._post(
            _EmbeddingClient(response),
            "https://provider.invalid/embeddings",
            ["업무 의미"],
            usage_log_path=usage_path,
            cost_budget=budget,
            agent_name="embedding:test",
        ))
        record = json.loads(usage_path.read_text(encoding="utf-8"))

    assert vectors == [[0.25, 0.75]]
    assert record["agent"] == "embedding:test"
    assert record["model"] == "text-embedding-3-small"
    assert record["input_tokens"] == 7
    assert record["output_tokens"] == 0
    assert record["cost_usd"] == 7 / 1_000_000 * 0.02
    assert budget.spent_usd == record["cost_usd"]


def test_paid_embedding_without_provider_usage_fails_closed() -> None:
    response = _EmbeddingResponse(usage=None)
    budget = CostBudget(limit_usd=1.0)
    with tempfile.TemporaryDirectory() as temporary:
        usage_path = Path(temporary) / "usage.jsonl"
        try:
            asyncio.run(text_embedder._post(
                _EmbeddingClient(response),
                "https://provider.invalid/embeddings",
                ["업무 의미"],
                usage_log_path=usage_path,
                cost_budget=budget,
            ))
        except text_embedder.EmbeddingError as error:
            assert "usage is missing" in str(error)
        else:
            raise AssertionError("missing paid embedding usage must fail")
        assert not usage_path.exists()
    assert budget.spent_usd == 0.0


def test_paid_embedding_http_failure_is_not_retried_or_split() -> None:
    client = _RetryableFailureClient()
    budget = CostBudget(limit_usd=1.0)

    try:
        asyncio.run(text_embedder._send(
            client,
            "https://provider.invalid/embeddings",
            ["첫 의미", "둘째 의미"],
            asyncio.Semaphore(1),
            usage_log_path=Path("unused.jsonl"),
            cost_budget=budget,
        ))
    except text_embedder.EmbeddingError as error:
        assert "retry and split are forbidden" in str(error)
    else:
        raise AssertionError("paid embedding transport failure must stop the run")

    assert client.calls == 1
    assert budget.spent_usd == 0.0
