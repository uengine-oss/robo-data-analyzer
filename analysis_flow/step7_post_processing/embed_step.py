"""embed_step — Table / Column / Function 의미 텍스트 임베딩 → Neo4j.

Plan §2 의 7 단계 흐름 마지막 — "테이블 description + 임베딩".

sentence-transformers 다국어 모델 (한국어 + 영어). 모델은 ``requirements.txt`` 의
``sentence-transformers`` 패키지의 기본 다국어 모델 사용.

흐름:
1. 모델 lazy load (singleton).
2. Table.description / Column.description / Function.summary 추출.
3. ``model.encode()`` → 벡터.
4. Neo4j UNWIND 배치로 ``embedding`` property SET.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.neo4j.neo4j_client import Neo4jClient


# 다국어 모델 (한국어 분석 결과 + 영어 코드 식별자 처리)
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model = None


def _get_model():
    """sentence-transformers 모델 1회 로드 후 캐시."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


async def embed_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """description / summary → 벡터 → Neo4j embedding property."""
    from shared.logging_and_stream.logger import log_info, log_warning
    import time as _time

    _STEP = "step7.embed"
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    neo: Neo4jClient | None = cfg.get("neo_client")
    if neo is None:
        log_warning(_STEP, "config_missing", "neo_client 누락 — step skip")
        return {}

    started = _time.monotonic()
    log_info(_STEP, "start",
             tables=len(state.get("tables") or []),
             columns=len(state.get("columns") or []),
             functions=len(state.get("functions") or []),
             model=_MODEL_NAME)

    # sentence-transformers 동기 호출 → asyncio.to_thread 로 격리
    table_updates, column_updates, function_updates = await asyncio.to_thread(
        _build_embeddings, state
    )

    if table_updates:
        await neo.run_batch_unwind(
            "UNWIND $items AS item "
            "MATCH (t:Table {fqn: item.fqn}) "
            "SET t.embedding = item.vector",
            table_updates,
        )
    if column_updates:
        await neo.run_batch_unwind(
            "UNWIND $items AS item "
            "MATCH (c:Column {fqn: item.fqn}) "
            "SET c.embedding = item.vector",
            column_updates,
        )
    if function_updates:
        await neo.run_batch_unwind(
            "UNWIND $items AS item "
            "MATCH (f:Function {function_id: item.function_id}) "
            "SET f.embedding = item.vector",
            function_updates,
        )

    log_info(
        _STEP, "done",
        table_embedded=len(table_updates),
        column_embedded=len(column_updates),
        function_embedded=len(function_updates),
        elapsed_sec=f"{_time.monotonic() - started:.2f}",
    )
    return {}


def _build_embeddings(state: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """state 의 description / summary 추출 → 벡터 생성."""
    model = _get_model()

    table_items: list[dict] = []
    for t in state.get("tables", []) or []:
        text = f"{t.name}: {t.description}".strip()
        if not text or text == f"{t.name}:":
            continue
        vec = model.encode(text, show_progress_bar=False).tolist()
        table_items.append({
            "fqn": f"{t.db}.{t.schema}.{t.name}",
            "vector": vec,
        })

    column_items: list[dict] = []
    for c in state.get("columns", []) or []:
        text = f"{c.name}: {c.description}".strip()
        if not text or text == f"{c.name}:":
            continue
        vec = model.encode(text, show_progress_bar=False).tolist()
        column_items.append({"fqn": c.fqn, "vector": vec})

    function_items: list[dict] = []
    for f in state.get("functions", []) or []:
        summary = getattr(f, "summary", None)
        if not summary:
            continue
        vec = model.encode(summary, show_progress_bar=False).tolist()
        function_items.append({"function_id": f.function_id, "vector": vec})

    return table_items, column_items, function_items
