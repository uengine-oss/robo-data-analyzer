"""Neo4j Table/Column 노드의 임베딩 생성 + 배치 저장.

DBMS phase_vector.py 에서만 쓰던 로직을 strategy 무관하게 재사용 가능하도록 분리.
Framework 도 향후 Table/Column 임베딩이 필요하면 본 모듈을 호출하면 된다.

원본 위치 (이전):
  - `analyzer/strategy/dbms/pipeline/phase_vector.py::run_phase_vector`
    의 임베딩 호출 + 배치 + Neo4j 업데이트 부분.

설계:
  - 임베딩 입력 텍스트 구성 함수(`build_table_text`, `build_column_text`)와
    배치 임베딩+저장 함수(`embed_and_persist_tables`, `embed_and_persist_columns`) 분리.
  - 호출자는 client(Neo4jClient) + EmbeddingClient 만 주입.
  - 모든 실패는 fail-fast (errlog + raise) — silent_fail 정책 정렬.

정책 정렬:
  - 빈 텍스트(설명도 없고 이름도 없는 비정상 입력) 는 errlog + raise.
  - 임베딩 호출 실패는 fail-fast (catch 후 errlog + raise).
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List

from analyzer.strategy.base_analyzer import AnalysisStats
from client.neo4j_client import Neo4jClient
from client.embedding_client import EmbeddingClient
from util.stream_emitter import emit_phase_event

logger = logging.getLogger(__name__)


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
# 배치 임베딩 + Neo4j 업데이트
# ═══════════════════════════════════════════════════════════════════════════


_TABLE_FETCH_QUERY = """
MATCH (t:Table)
WHERE t.embedding IS NULL
RETURN t.name AS name, t.schema AS schema, t.db AS db,
       t.description AS description, t.analyzed_description AS analyzed_description
"""

_TABLE_UPDATE_QUERY = (
    "MATCH (t:Table {name: $name}) SET t.embedding = $embedding"
)

_COLUMN_FETCH_QUERY = """
MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
WHERE c.embedding IS NULL
RETURN c.name AS col_name, t.name AS table_name,
       c.description AS description, c.analyzed_description AS analyzed_description, c.dtype AS dtype
"""

_COLUMN_UPDATE_QUERY = (
    "MATCH (t:Table {name: $table_name})-[:HAS_COLUMN]->(c:Column {name: $col_name}) "
    "SET c.embedding = $embedding"
)


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


async def vectorize_tables(
    client: Neo4jClient,
    embedder: EmbeddingClient,
    *,
    stats: AnalysisStats,
    batch_size: int = 32,
    phase_num: int = 4,
    progress_start: int = 25,
    progress_end: int = 100,
) -> AsyncGenerator[bytes, None]:
    """Table 임베딩 — embedding=NULL 인 노드 전체를 batch 단위로 처리.

    `stats.tables_vectorized` 를 업데이트한 만큼 누적 갱신.

    Yields:
        emit_phase_event 바이트 (진행률 갱신용)
    """
    fetched = await client.execute_queries([_TABLE_FETCH_QUERY])
    rows: List[Dict[str, Any]] = fetched[0] if fetched and fetched[0] else []
    total = len(rows)
    logger.info("[VECTORIZER:TABLE] 시작 | total=%d batch_size=%d", total, batch_size)
    if not rows:
        return

    progress_span = max(1, progress_end - progress_start)
    for i in range(0, total, batch_size):
        chunk = rows[i:i + batch_size]
        texts = [build_table_text(r) for r in chunk]
        embeddings = await _embed_batch(embedder, texts, "TABLE")
        update_queries = [
            {
                "query": _TABLE_UPDATE_QUERY,
                "params": {"name": r["name"], "embedding": emb},
            }
            for r, emb in zip(chunk, embeddings)
        ]
        if update_queries:
            await client.execute_queries(update_queries)
            stats.tables_vectorized += len(update_queries)
        progress = progress_start + int((i + len(chunk)) / total * progress_span)
        yield emit_phase_event(phase_num, "벡터라이징", "in_progress", min(progress, progress_end))


async def vectorize_columns(
    client: Neo4jClient,
    embedder: EmbeddingClient,
    *,
    batch_size: int = 32,
) -> int:
    """Column 임베딩 — embedding=NULL 인 :Column 노드 전체를 batch 단위로 처리.

    Returns:
        업데이트된 컬럼 수.
    """
    fetched = await client.execute_queries([_COLUMN_FETCH_QUERY])
    rows: List[Dict[str, Any]] = fetched[0] if fetched and fetched[0] else []
    total = len(rows)
    logger.info("[VECTORIZER:COLUMN] 시작 | total=%d batch_size=%d", total, batch_size)
    if not rows:
        return 0

    updated = 0
    for i in range(0, total, batch_size):
        chunk = rows[i:i + batch_size]
        texts = [build_column_text(r) for r in chunk]
        embeddings = await _embed_batch(embedder, texts, "COLUMN")
        update_queries = [
            {
                "query": _COLUMN_UPDATE_QUERY,
                "params": {
                    "table_name": r["table_name"],
                    "col_name": r["col_name"],
                    "embedding": emb,
                },
            }
            for r, emb in zip(chunk, embeddings)
        ]
        if update_queries:
            await client.execute_queries(update_queries)
            updated += len(update_queries)
    return updated


__all__ = [
    "build_table_text",
    "build_column_text",
    "vectorize_tables",
    "vectorize_columns",
]
