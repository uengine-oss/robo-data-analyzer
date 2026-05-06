"""Neo4j Table/Column 노드의 임베딩 생성 + 배치 저장.

DBMS / Framework 양쪽에서 사용. Phase 4 (벡터라이징) 의 핵심 모듈.

설계:
  - 임베딩 입력 텍스트 구성 (`build_table_text`, `build_column_text`)
  - 배치 단위 OpenAI 임베딩 호출 + UNWIND 단일 Cypher 로 Neo4j 일괄 업데이트
  - `asyncio.gather` 로 배치 동시 실행 (`_BATCH_CONCURRENCY` 개) — Semaphore 로 OpenAI rate limit 보호
  - 모든 실패는 fail-fast (errlog + raise) — silent_fail 정책 정렬
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List

from analyzer.strategy.base_analyzer import AnalysisStats
from client.neo4j_client import Neo4jClient
from client.embedding_client import EmbeddingClient
from util.stream_emitter import emit_phase_event, emit_user_message

logger = logging.getLogger(__name__)


# OpenAI text-embedding-3-small 토큰 한도 8191/입력. 256개 항목 batch 가 안전.
_DEFAULT_BATCH_SIZE = 256
# 동시 OpenAI 호출 개수 (rate limit 보호). 16 이면 일반 5000 RPM 한도 안에서 안전 범위.
# rate limit 에러 발생 시 8 로 회귀 검토.
_BATCH_CONCURRENCY = 16


# ═══════════════════════════════════════════════════════════════════════════
# 텍스트 구성 — 임베딩 입력으로 사용할 자연어 표현
# ═══════════════════════════════════════════════════════════════════════════


def build_table_text(table_row: Dict[str, Any]) -> str:
    """Table 노드의 임베딩 입력 텍스트 구성.

    우선순위: analyzed_description > description > name. 모두 비면 errlog + raise.
    """
    desc = table_row.get("analyzed_description") or table_row.get("description") or table_row.get("name")
    if not desc:
        logger.error(
            "[VECTORIZER:TABLE] 임베딩 텍스트 구성 실패 — analyzed_description/description/name 모두 빈값 | row=%r",
            table_row,
        )
        raise ValueError(f"Table 임베딩 입력 누락: {table_row}")
    return desc


def build_column_text(column_row: Dict[str, Any]) -> str:
    """Column 노드의 임베딩 입력 텍스트 구성.

    형식: ``{table_name}.{col_name}: {description}`` (description 없으면 "table.col" 만).
    table_name / col_name 둘 중 하나라도 비면 errlog + raise.
    """
    table = column_row.get("table_name")
    col = column_row.get("col_name")
    if not table or not col:
        logger.error(
            "[VECTORIZER:COLUMN] 임베딩 텍스트 구성 실패 — table_name/col_name 누락 | row=%r",
            column_row,
        )
        raise ValueError(f"Column 임베딩 입력 누락: {column_row}")
    desc = column_row.get("analyzed_description") or column_row.get("description") or ""
    return f"{table}.{col}: {desc}" if desc else f"{table}.{col}"


# ═══════════════════════════════════════════════════════════════════════════
# Cypher 쿼리 — UNWIND 단일 호출 (32 round-trip → 1 round-trip)
# ═══════════════════════════════════════════════════════════════════════════


_TABLE_FETCH_QUERY = """
MATCH (t:Table)
WHERE t.embedding IS NULL
RETURN t.name AS name, t.schema AS schema, t.db AS db,
       t.description AS description, t.analyzed_description AS analyzed_description
"""


_TABLE_UPDATE_UNWIND = """
UNWIND $items AS item
MATCH (t:Table {name: item.name})
SET t.embedding = item.embedding
"""


_COLUMN_FETCH_QUERY = """
MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
WHERE c.embedding IS NULL
RETURN c.name AS col_name, t.name AS table_name,
       c.description AS description, c.analyzed_description AS analyzed_description, c.dtype AS dtype
"""


_COLUMN_UPDATE_UNWIND = """
UNWIND $items AS item
MATCH (t:Table {name: item.table_name})-[:HAS_COLUMN]->(c:Column {name: item.col_name})
SET c.embedding = item.embedding
"""


# ═══════════════════════════════════════════════════════════════════════════
# 배치 임베딩 + Neo4j UNWIND 업데이트 (병렬)
# ═══════════════════════════════════════════════════════════════════════════


async def _embed_batch(embedder: EmbeddingClient, texts: List[str], label: str) -> List[List[float]]:
    """단일 배치 임베딩 호출. 실패 시 fail-fast (errlog + raise)."""
    try:
        return await embedder.embed_batch(texts)
    except Exception as exc:
        logger.error(
            "[VECTORIZER:%s] embedding 호출 실패 | batch_size=%d | %s: %s",
            label, len(texts), type(exc).__name__, exc, exc_info=True,
        )
        raise RuntimeError(
            f"임베딩 호출 실패 ({label}, batch_size={len(texts)}): {type(exc).__name__}: {exc}"
        ) from exc


async def _process_one_batch(
    chunk: List[Dict[str, Any]],
    embedder: EmbeddingClient,
    client: Neo4jClient,
    label: str,
    update_query: str,
    text_builder,
    item_builder,
    semaphore: asyncio.Semaphore,
) -> int:
    """1배치 처리 — 임베딩 호출 + UNWIND 1쿼리로 Neo4j 업데이트. 처리 건수 반환."""
    async with semaphore:
        texts = [text_builder(r) for r in chunk]
        embeddings = await _embed_batch(embedder, texts, label)
        items = [item_builder(r, emb) for r, emb in zip(chunk, embeddings)]
        if not items:
            return 0
        await client.execute_queries([{"query": update_query, "params": {"items": items}}])
        return len(items)


async def vectorize_tables(
    client: Neo4jClient,
    embedder: EmbeddingClient,
    *,
    stats: AnalysisStats,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    phase_num: int = 4,
    progress_start: int = 25,
    progress_end: int = 100,
) -> AsyncGenerator[bytes, None]:
    """Table 임베딩 — embedding=NULL 인 노드 전체를 batch + 병렬로 처리.

    각 batch:
      - OpenAI 임베딩 N개 → 1 API call
      - Neo4j UNWIND → 1 Cypher
    `_BATCH_CONCURRENCY` 개 batch 가 동시 실행 (Semaphore 로 OpenAI rate limit 보호).
    `stats.tables_vectorized` 를 처리한 만큼 누적 갱신. 진행 상황은 user_message 로 emit.
    """
    fetched = await client.execute_queries([_TABLE_FETCH_QUERY])
    rows: List[Dict[str, Any]] = fetched[0] if fetched and fetched[0] else []
    total = len(rows)
    logger.info("[VECTORIZER:TABLE] 시작 | total=%d batch_size=%d concurrency=%d", total, batch_size, _BATCH_CONCURRENCY)
    if not rows:
        yield emit_user_message("  ├─ 테이블 임베딩 대상 없음 (skip)")
        return

    yield emit_user_message(
        f"  ├─ {total}개 테이블 임베딩 시작 (배치 {batch_size}, 동시 {_BATCH_CONCURRENCY})"
    )

    chunks = [rows[i:i + batch_size] for i in range(0, total, batch_size)]
    semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)
    progress_span = max(1, progress_end - progress_start)
    done_total = 0

    tasks = [
        asyncio.create_task(_process_one_batch(
            chunk, embedder, client, "TABLE",
            _TABLE_UPDATE_UNWIND, build_table_text,
            lambda r, emb: {"name": r["name"], "embedding": emb},
            semaphore,
        ))
        for chunk in chunks
    ]

    for completed_task in asyncio.as_completed(tasks):
        processed = await completed_task
        stats.tables_vectorized += processed
        done_total += processed
        progress = progress_start + int(done_total / total * progress_span)
        yield emit_user_message(f"  ├─ [{done_total}/{total}] 테이블 임베딩")
        yield emit_phase_event(phase_num, "벡터라이징", "in_progress", min(progress, progress_end))


async def vectorize_columns(
    client: Neo4jClient,
    embedder: EmbeddingClient,
    *,
    stats: AnalysisStats,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> AsyncGenerator[bytes, None]:
    """Column 임베딩 — embedding=NULL 인 :Column 노드 전체를 batch + 병렬로 처리.

    `stats.columns_vectorized` 를 처리한 만큼 누적 갱신. 진행 상황은 user_message 로 emit.
    """
    fetched = await client.execute_queries([_COLUMN_FETCH_QUERY])
    rows: List[Dict[str, Any]] = fetched[0] if fetched and fetched[0] else []
    total = len(rows)
    logger.info("[VECTORIZER:COLUMN] 시작 | total=%d batch_size=%d concurrency=%d", total, batch_size, _BATCH_CONCURRENCY)
    if not rows:
        yield emit_user_message("  ├─ 컬럼 임베딩 대상 없음 (skip)")
        return

    yield emit_user_message(
        f"  ├─ {total}개 컬럼 임베딩 시작 (배치 {batch_size}, 동시 {_BATCH_CONCURRENCY})"
    )

    chunks = [rows[i:i + batch_size] for i in range(0, total, batch_size)]
    semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)
    done_total = 0

    tasks = [
        asyncio.create_task(_process_one_batch(
            chunk, embedder, client, "COLUMN",
            _COLUMN_UPDATE_UNWIND, build_column_text,
            lambda r, emb: {"table_name": r["table_name"], "col_name": r["col_name"], "embedding": emb},
            semaphore,
        ))
        for chunk in chunks
    ]

    for completed_task in asyncio.as_completed(tasks):
        processed = await completed_task
        stats.columns_vectorized += processed
        done_total += processed
        yield emit_user_message(f"  ├─ [{done_total}/{total}] 컬럼 임베딩")


__all__ = [
    "build_table_text",
    "build_column_text",
    "vectorize_tables",
    "vectorize_columns",
]
