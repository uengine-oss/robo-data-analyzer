"""embed_step — Table / Column / Function 의미 텍스트 임베딩 → Neo4j.

흐름:
1. ``Table.description`` / ``Column.description`` / ``Function.summary`` 텍스트 수집.
2. ``shared.llm.embedder.encode_texts`` 로 배치 인코딩 — 어디서 어떻게 임베딩을
   받는지는 LLM 영역 책임. 이 step 은 텍스트 → 벡터의 mechanic 만.
3. Neo4j UNWIND 배치로 ``embedding`` property SET.
4. state 의 노드 객체에 embedding 박은 새 인스턴스로 교체 (artifact 검증 정합).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.config.env_catalog import ENV
from shared.llm import embedder
from shared.log.logger import log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.workflow.step_utils import step_config


_STEP = "step7.embed"


def _embedding_query(label: str, key: str) -> str:
    """``embedding`` property SET UNWIND 쿼리 — 라벨·식별키만 다르고 형태 동일."""
    return (
        f"UNWIND $items AS item "
        f"MATCH (n:{label} {{{key}: item.{key}}}) "
        f"SET n.embedding = item.vector"
    )


async def embed_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """description / summary → 벡터 → Neo4j embedding property + state 반영.

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
             backend=embedder.describe())

    # 동기 호출 → asyncio.to_thread 로 격리 (현재 backend 는 로컬 sentence-transformers).
    table_updates, column_updates, function_updates = await asyncio.to_thread(
        _build_embeddings, state
    )

    for label, key, items in (
        ("TABLE", "fqn", table_updates),
        ("COLUMN", "fqn", column_updates),
        ("FUNCTION", "function_id", function_updates),
    ):
        if items:
            await neo.run_batch_unwind(_embedding_query(label, key), items)

    updated_tables = _attach_embedding(state.get("tables", []), table_updates, "fqn")
    updated_columns = _attach_embedding(state.get("columns", []), column_updates, "fqn")
    updated_functions = _attach_embedding(state.get("functions", []), function_updates, "function_id")

    log_info(
        _STEP, "done",
        table_embedded=len(table_updates),
        column_embedded=len(column_updates),
        function_embedded=len(function_updates),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "tables": updated_tables,
        "columns": updated_columns,
        "functions": updated_functions,
    }


def _attach_embedding(nodes: list, updates: list[dict], key_attr: str) -> list:
    """``updates`` 의 vector 를 같은 키의 노드에 박은 새 인스턴스 리스트.

    ``updates`` 형식: ``[{<key_attr>: ..., "vector": [...]}, ...]``.
    매치 안 된 노드는 결과에서 제외 — reducer 가 기존 노드는 그대로 유지.
    """
    key_to_vec = {u[key_attr]: u["vector"] for u in updates}
    return [
        replace(n, embedding=tuple(key_to_vec[getattr(n, key_attr)]))
        for n in nodes
        if getattr(n, key_attr) in key_to_vec
    ]


def _build_embeddings(state: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """state 의 description / summary 수집 → 그룹별 배치 인코딩 → updates dict.

    그룹별 텍스트 수집 → ``embedder.encode_texts`` 1회 호출씩 → 키-벡터 쌍 dict.
    """
    table_pending = _collect_table_texts(state.get("tables", []))
    column_pending = _collect_column_texts(state.get("columns", []))
    function_pending = _collect_function_texts(state.get("functions", []))

    return (
        _encode_pairs(table_pending, "fqn"),
        _encode_pairs(column_pending, "fqn"),
        _encode_pairs(function_pending, "function_id"),
    )


def _collect_table_texts(tables: list) -> list[tuple[str, str]]:
    """``Table`` 들의 (fqn, ``"name: description"``) 쌍. description 빈 것은 제외."""
    out: list[tuple[str, str]] = []
    for t in tables:
        desc = (t.description or "").strip()
        if not desc:
            continue
        out.append((t.fqn, f"{t.name}: {desc}"))
    return out


def _collect_column_texts(columns: list) -> list[tuple[str, str]]:
    """``Column`` 들의 (fqn, ``"<table>.<column>: description"``) 쌍.

    테이블 prefix — 동명 컬럼(id/name 등) 벡터가 테이블 구분 없이 뭉치는 것 방지.
    """
    out: list[tuple[str, str]] = []
    for c in columns:
        desc = (c.description or "").strip()
        if not desc:
            continue
        parts = c.fqn.split(".")
        label = f"{parts[-2]}.{c.name}" if len(parts) >= 2 else c.name
        out.append((c.fqn, f"{label}: {desc}"))
    return out


def _collect_function_texts(functions: list) -> list[tuple[str, str]]:
    """``Function`` 들의 (function_id, summary) 쌍. summary 빈 것은 제외."""
    out: list[tuple[str, str]] = []
    for f in functions:
        summary = getattr(f, "summary", None)
        if not summary:
            continue
        out.append((f.function_id, summary))
    return out


def _encode_pairs(pending: list[tuple[str, str]], key_name: str) -> list[dict]:
    """``[(키, 텍스트)]`` → ``[{key_name: 키, "vector": [...]}]``. embedder 1회 호출."""
    if not pending:
        return []
    texts = [text for _, text in pending]
    vectors = embedder.encode_texts(texts)
    return [
        {key_name: key, "vector": vec}
        for (key, _), vec in zip(pending, vectors)
    ]
