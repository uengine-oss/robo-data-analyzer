"""임베딩 — 텍스트를 벡터로. 어디서 계산하는지는 이 모듈만 안다.

호출자(``build_search_segments_step``)는 ``encode_texts(texts)`` 한 줄만 부른다.

기본 공급자는 사내 임베딩 서버다. 이 서버는 요청 하나의 **총 토큰**이 크면 거부하고(개수가
아니다), 그 한계가 부하에 따라 흔들린다. 그래서 개수 고정 배치가 아니라 **문자 예산으로 묶고**,
여러 요청을 **동시에** 보내며, 거부당하면 **반으로 쪼개** 재시도한다. 단건마저 거부되면
지수 백오프로 기다린다. 끝내 실패하면 raise — 임베딩 없는 노드는 검색에서 영원히 사라지므로
조용히 건너뛰지 않는다.

로컬 인코딩은 GPU 없는 장비에서 수백 배 느려 폴백 전용이다.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Callable, List, Sequence

import httpx

from shared.config.settings import SETTINGS
from llm.configs.loader import load_llm_config
from llm.usage.cost_budget import CostBudget
from llm.usage.pricing import compute_cost
from llm.usage.token_usage import TokenUsageRecord
from llm.usage.usage_jsonl_writer import persist_usage_and_charge_budget
from shared.events.runtime_logger import log_info, log_warning

Vector = List[float]

_RETRYABLE = frozenset({413, 424, 429, 500, 502, 503})
_STEP = "llm.embedder"


class EmbeddingError(RuntimeError):
    """임베딩 생성 실패 — 노드 유실을 막기 위해 반드시 상위로 전파된다."""


def fingerprint() -> str:
    """질의와 노드가 같은 벡터 공간에 있는지 대조하는 지문."""
    return f"{SETTINGS.embedding.provider}:{SETTINGS.embedding.model}"


def encode_texts(
    texts: Sequence[str],
    *,
    usage_log_path: Path | None = None,
    cost_budget: CostBudget | None = None,
    agent_name: str = "embedding",
) -> list[Vector]:
    """텍스트 → 벡터. 입력 순서를 그대로 유지한다. 빈 입력은 모델 로드 없이 빈 결과."""
    if not texts:
        return []
    encode = _ENCODERS.get(SETTINGS.embedding.provider)
    if encode is None:
        raise EmbeddingError(
            f"알 수 없는 임베딩 공급자: {SETTINGS.embedding.provider!r} (가능: {sorted(_ENCODERS)})"
        )
    exact_texts = list(texts)
    if SETTINGS.embedding.provider == "openai" and usage_log_path is not None:
        if cost_budget is None:
            raise EmbeddingError(
                "paid embedding usage requires the run CostBudget"
            )
        vectors = _encode_remote(
            exact_texts,
            usage_log_path=usage_log_path,
            cost_budget=cost_budget,
            agent_name=agent_name,
        )
    else:
        vectors = encode(exact_texts)
    if len(vectors) != len(exact_texts):
        raise EmbeddingError(
            "임베딩 결과 수 불일치: "
            f"입력={len(exact_texts)}, 결과={len(vectors)} — 순서 대응 불가능"
        )
    return vectors


# ── 사내 임베딩 서버 (기본) ────────────────────────────────

def _budget_chunks(texts: list[str], budget: int) -> list[list[int]]:
    """문자 예산으로 인덱스를 묶는다 — 짧은 텍스트는 여러 개, 긴 텍스트는 혼자."""
    chunks: list[list[int]] = []
    current: list[int] = []
    size = 0
    for i, text in enumerate(texts):
        if current and size + len(text) > budget:
            chunks.append(current)
            current, size = [], 0
        current.append(i)
        size += len(text)
    if current:
        chunks.append(current)
    return chunks


def _resolve_endpoint() -> tuple[str, str]:
    """임베딩 서버 주소·키. 미지정이면 분석용 LLM 과 같은 서버를 쓴다."""
    api_base = SETTINGS.embedding.api_base
    api_key = SETTINGS.embedding.api_key
    if not (api_base and api_key):
        llm = load_llm_config(SETTINGS.analysis_llm.config)
        api_base = api_base or llm.api_base
        api_key = api_key or SETTINGS.analysis_llm.api_key
    if not api_base:
        raise EmbeddingError("임베딩 서버 주소 없음 — ROBO_EMBED_API_BASE 또는 LLM config 의 api_base")
    return api_base.rstrip("/") + "/embeddings", api_key


async def _post(
    client: httpx.AsyncClient,
    url: str,
    inputs: list[str],
    *,
    usage_log_path: Path | None = None,
    cost_budget: CostBudget | None = None,
    agent_name: str = "embedding",
) -> list[Vector]:
    started = time.monotonic()
    resp = await client.post(url, json={"model": SETTINGS.embedding.model, "input": inputs})
    resp.raise_for_status()
    payload = resp.json()
    vectors = [item["embedding"] for item in payload["data"]]
    if usage_log_path is not None:
        if cost_budget is None:
            raise EmbeddingError("paid embedding usage requires the run CostBudget")
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise EmbeddingError(
                "embedding response usage is missing; paid call cannot be accounted"
            )
        input_tokens = usage.get("prompt_tokens")
        total_tokens = usage.get("total_tokens")
        if (
            isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or input_tokens < 0
            or isinstance(total_tokens, bool)
            or not isinstance(total_tokens, int)
            or total_tokens < input_tokens
        ):
            raise EmbeddingError(
                "embedding response usage has invalid prompt_tokens/total_tokens"
            )
        model = payload.get("model") or SETTINGS.embedding.model
        if not isinstance(model, str) or not model.strip():
            raise EmbeddingError("embedding response model is missing")
        record = TokenUsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=0,
            total_tokens=total_tokens,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        cost_usd = compute_cost(record)
        persist_usage_and_charge_budget(
            usage_log_path,
            agent_name,
            record,
            cost_usd,
            cost_budget,
        )
    return vectors


async def _send(
    client: httpx.AsyncClient,
    url: str,
    texts: list[str],
    gate: asyncio.Semaphore,
    *,
    usage_log_path: Path | None = None,
    cost_budget: CostBudget | None = None,
    agent_name: str = "embedding",
) -> list[Vector]:
    """한 묶음 전송. 서버 거부는 분할·백오프로 흡수하고, 끝내 실패하면 raise."""
    paid_accounted_request = usage_log_path is not None
    attempt_limit = 1 if paid_accounted_request else SETTINGS.embedding.max_retries
    for attempt in range(attempt_limit):
        try:
            async with gate:
                return await _post(
                    client,
                    url,
                    texts,
                    usage_log_path=usage_log_path,
                    cost_budget=cost_budget,
                    agent_name=agent_name,
                )
        except httpx.HTTPStatusError as exc:
            if paid_accounted_request:
                raise EmbeddingError(
                    "paid embedding request failed; retry and split are forbidden: "
                    f"HTTP {exc.response.status_code}"
                ) from exc
            if exc.response.status_code not in _RETRYABLE:
                raise EmbeddingError(f"임베딩 서버 오류 {exc.response.status_code}") from exc
            if len(texts) > 1:
                half = len(texts) // 2
                head, tail = await asyncio.gather(
                    _send(
                        client, url, texts[:half], gate,
                        usage_log_path=usage_log_path,
                        cost_budget=cost_budget,
                        agent_name=agent_name,
                    ),
                    _send(
                        client, url, texts[half:], gate,
                        usage_log_path=usage_log_path,
                        cost_budget=cost_budget,
                        agent_name=agent_name,
                    ),
                )
                return head + tail
            await asyncio.sleep(0.4 * 2**attempt)      # 단건 거부 = 부하. 물러났다 다시.
    raise EmbeddingError(
        f"임베딩 재시도 {SETTINGS.embedding.max_retries}회 실패 (길이 {len(texts[0])}자) — 노드 유실 방지를 위해 중단"
    )


async def _encode_remote_async(
    texts: list[str],
    *,
    usage_log_path: Path | None = None,
    cost_budget: CostBudget | None = None,
    agent_name: str = "embedding",
) -> list[Vector]:
    url, api_key = _resolve_endpoint()
    chunks = _budget_chunks(texts, SETTINGS.embedding.char_budget)
    gate = asyncio.Semaphore(SETTINGS.embedding.concurrency)
    log_info(_STEP, "start", provider=SETTINGS.embedding.provider, model=SETTINGS.embedding.model,
             texts=len(texts), requests=len(chunks), concurrency=SETTINGS.embedding.concurrency)

    async with httpx.AsyncClient(
        timeout=SETTINGS.embedding.timeout_seconds, headers={"Authorization": f"Bearer {api_key}"}
    ) as client:
        results = await asyncio.gather(
            *(
                _send(
                    client,
                    url,
                    [texts[i] for i in chunk],
                    gate,
                    usage_log_path=usage_log_path,
                    cost_budget=cost_budget,
                    agent_name=agent_name,
                )
                for chunk in chunks
            )
        )

    vectors: list[Vector | None] = [None] * len(texts)
    for chunk, chunk_vectors in zip(chunks, results):
        for i, vector in zip(chunk, chunk_vectors):
            vectors[i] = vector
    missing = [i for i, v in enumerate(vectors) if v is None]
    if missing:
        raise EmbeddingError(f"임베딩 응답 누락 {len(missing)}건 — 서버가 요청보다 적게 반환")
    return vectors  # type: ignore[return-value]


def _encode_remote(
    texts: list[str],
    *,
    usage_log_path: Path | None = None,
    cost_budget: CostBudget | None = None,
    agent_name: str = "embedding",
) -> list[Vector]:
    return asyncio.run(_encode_remote_async(
        texts,
        usage_log_path=usage_log_path,
        cost_budget=cost_budget,
        agent_name=agent_name,
    ))


# ── 로컬 인코딩 (폴백 전용) ────────────────────────────────

_local_model = None


def _encode_local(texts: list[str]) -> list[Vector]:
    global _local_model
    if _local_model is None:
        log_warning(_STEP, "local_provider",
                    "로컬 인코딩은 GPU 없는 장비에서 수백 배 느리다 — 개발용으로만 쓸 것")
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer(SETTINGS.embedding.model)
    return _local_model.encode(texts, normalize_embeddings=True).tolist()


_ENCODERS: dict[str, Callable[[list[str]], list[Vector]]] = {
    "sglang": _encode_remote,
    "openai": _encode_remote,          # OpenAI 호환 스펙 — 같은 경로
    "local": _encode_local,
}
