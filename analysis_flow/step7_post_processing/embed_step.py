"""embed_step — Table / Column / Function 의미 텍스트 임베딩 → Neo4j.

Plan §2 의 7 단계 흐름 마지막 — "테이블 description + 임베딩".

sentence-transformers 다국어 모델 (한국어 + 영어). 모델은 ``requirements.txt`` 의
``sentence-transformers`` 패키지의 기본 다국어 모델 사용.

흐름:
1. 모델 lazy load (singleton).
2. Table.description / Column.description / Function.summary 추출.
3. ``model.encode([텍스트들])`` 그룹별 배치 인코딩 → 벡터 (1개씩 X).
4. Neo4j UNWIND 배치로 ``embedding`` property SET.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.config.env_catalog import ENV
from shared.log.logger import log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.workflow.step_utils import step_config


# 다국어 모델 (한국어 분석 결과 + 영어 코드 식별자 처리)
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_STEP = "step7.embed"

_model = None


def _get_model():
    """sentence-transformers 모델 1회 로드 후 캐시."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def _embedding_query(label: str, key: str) -> str:
    """``embedding`` property SET UNWIND 쿼리 — 라벨·식별키만 다르고 형태 동일."""
    return (
        f"UNWIND $items AS item "
        f"MATCH (n:{label} {{{key}: item.{key}}}) "
        f"SET n.embedding = item.vector"
    )


async def embed_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """description / summary → 벡터 → Neo4j embedding property.

    on/off 토글 — ``ROBO_PIPELINE_EMBED_ENABLED`` (기본 ON). 비활성 시 step skip.
    """
    if not ENV.pipeline.embed_enabled:
        log_info(_STEP, "skip", reason="임베딩 비활성화 (ROBO_PIPELINE_EMBED_ENABLED=false)")
        return {}

    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")
    if neo is None:
        log_warning(_STEP, "config_missing", "neo_client 누락 — step skip")
        return {}

    started = time.monotonic()
    log_info(_STEP, "start",
             tables=len(state.get("tables") or []),
             columns=len(state.get("columns") or []),
             functions=len(state.get("functions") or []),
             model=_MODEL_NAME)

    # sentence-transformers 동기 호출 → asyncio.to_thread 로 격리
    table_updates, column_updates, function_updates = await asyncio.to_thread(
        _build_embeddings, state
    )

    # 라벨·식별키만 다른 같은 형태 — _embedding_query 로 한 곳에서.
    for label, key, items in (
        ("TABLE", "fqn", table_updates),
        ("COLUMN", "fqn", column_updates),
        ("FUNCTION", "function_id", function_updates),
    ):
        if items:
            await neo.run_batch_unwind(_embedding_query(label, key), items)

    log_info(
        _STEP, "done",
        table_embedded=len(table_updates),
        column_embedded=len(column_updates),
        function_embedded=len(function_updates),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {}


def _encode_batch(model, pending: list[tuple[str, str]], key_name: str) -> list[dict]:
    """``[(키, 텍스트)]`` → ``[{key_name: 키, "vector": 벡터}]``. 배치 인코딩 1회.

    텍스트를 1개씩 ``encode`` 하지 않고 목록을 한 번에 넘긴다 —
    sentence-transformers 배치 최적화로 개수가 많을수록 큰 차이.
    """
    if not pending:
        return []
    texts = [text for _, text in pending]
    vectors = model.encode(texts, show_progress_bar=False).tolist()
    return [
        {key_name: key, "vector": vec}
        for (key, _), vec in zip(pending, vectors)
    ]


def _build_embeddings(state: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """state 의 description / summary 추출 → 그룹별 배치 인코딩 → 벡터."""
    model = _get_model()

    # (식별키, 임베딩 텍스트) 수집 — 그룹별로 모아 한 번에 인코딩.
    table_pending: list[tuple[str, str]] = []
    for t in state.get("tables", []) or []:
        text = f"{t.name}: {t.description}".strip()
        if not text or text == f"{t.name}:":
            continue
        table_pending.append((t.fqn, text))

    column_pending: list[tuple[str, str]] = []
    for c in state.get("columns", []) or []:
        desc = (c.description or "").strip()
        if not desc:
            continue
        # 테이블 prefix — 동명 컬럼(id/name 등) 벡터가 테이블 구분 없이 뭉치는 것 방지.
        parts = c.fqn.split(".")
        label = f"{parts[-2]}.{c.name}" if len(parts) >= 2 else c.name
        column_pending.append((c.fqn, f"{label}: {desc}"))

    function_pending: list[tuple[str, str]] = []
    for f in state.get("functions", []) or []:
        summary = getattr(f, "summary", None)
        if not summary:
            continue
        function_pending.append((f.function_id, summary))

    return (
        _encode_batch(model, table_pending, "fqn"),
        _encode_batch(model, column_pending, "fqn"),
        _encode_batch(model, function_pending, "function_id"),
    )
