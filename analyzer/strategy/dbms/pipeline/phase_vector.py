"""DBMS Phase 4: 벡터라이징 — 테이블/컬럼 임베딩 생성."""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from util.ndjson_stream import emit_message, emit_phase_event

logger = logging.getLogger(__name__)


async def run_phase_vector(
    ctx: Any,  # DbmsPhaseContext
    phase_num: int = 4,
) -> AsyncGenerator[bytes, None]:
    """테이블/컬럼 벡터라이징 (배치 최적화)."""
    from openai import AsyncOpenAI
    from client.embedding_client import EmbeddingClient
    client = ctx.client

    api_key = settings.openai_api_key or ctx.api_key
    if not api_key:
        yield emit_message("   ⚠️ OpenAI API 키가 없어 벡터라이징을 건너뜁니다")
        return

    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)

    # ── 1) 테이블 벡터라이징 ──
    table_query = """
    MATCH (t:Table)
    WHERE t.embedding IS NULL
    RETURN t.name AS name, t.schema AS schema, t.db AS db,
           t.description AS description, t.analyzed_description AS analyzed_description
    """
    results = await client.execute_queries([table_query])
    tables = results[0] if results and results[0] else []

    if tables:
        yield emit_message(f"   🔢 테이블 벡터라이징: {len(tables)}개")
        batch_size = 50
        for i in range(0, len(tables), batch_size):
            batch = tables[i:i + batch_size]
            texts = []
            for t in batch:
                desc = t.get("analyzed_description") or t.get("description") or t.get("name", "")
                texts.append(desc)

            try:
                embeddings = await embedding_client.create_embeddings(texts)
                update_queries = []
                for j, t in enumerate(batch):
                    if j < len(embeddings) and embeddings[j]:
                        update_queries.append({
                            "query": "MATCH (t:Table {name: $name}) SET t.embedding = $embedding",
                            "params": {"name": t["name"], "embedding": embeddings[j]},
                        })
                if update_queries:
                    await client.execute_queries([q["query"] for q in update_queries],
                                                  [q["params"] for q in update_queries])
            except Exception as e:
                logger.error("[PHASE:4:VECTOR] 테이블 벡터라이징 실패 | batch=%d error=%s", i // batch_size, e)

            batch_progress = 25 + int((i + len(batch)) / len(tables) * 75)
            yield emit_phase_event(phase_num, "벡터라이징", "in_progress", min(batch_progress, 100))

    # ── 2) 컬럼 벡터라이징 ──
    col_query = """
    MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
    WHERE c.embedding IS NULL
    RETURN c.name AS col_name, t.name AS table_name,
           c.description AS description, c.analyzed_description AS analyzed_description, c.dtype AS dtype
    """
    col_results = await client.execute_queries([col_query])
    columns = col_results[0] if col_results and col_results[0] else []

    if columns:
        yield emit_message(f"   🔢 컬럼 벡터라이징: {len(columns)}개")
        batch_size = 50
        for i in range(0, len(columns), batch_size):
            batch = columns[i:i + batch_size]
            texts = []
            for c in batch:
                desc = c.get("analyzed_description") or c.get("description") or ""
                if desc:
                    texts.append(f"{c.get('table_name', '')}.{c.get('col_name', '')}: {desc}")
                else:
                    texts.append(f"{c.get('table_name', '')}.{c.get('col_name', '')}")

            try:
                embeddings = await embedding_client.create_embeddings(texts)
                update_queries = []
                for j, c in enumerate(batch):
                    if j < len(embeddings) and embeddings[j]:
                        update_queries.append({
                            "query": (
                                "MATCH (t:Table {name: $table_name})-[:HAS_COLUMN]->(c:Column {name: $col_name}) "
                                "SET c.embedding = $embedding"
                            ),
                            "params": {"table_name": c["table_name"], "col_name": c["col_name"], "embedding": embeddings[j]},
                        })
                if update_queries:
                    await client.execute_queries([q["query"] for q in update_queries],
                                                  [q["params"] for q in update_queries])
            except Exception as e:
                logger.error("[PHASE:4:VECTOR] 컬럼 벡터라이징 실패 | batch=%d error=%s", i // batch_size, e)

    total_vectorized = len(tables) + len(columns)
    yield emit_message(f"   ✅ 벡터라이징 완료: 테이블 {len(tables)}개, 컬럼 {len(columns)}개")
    yield emit_phase_event(phase_num, "벡터라이징", "completed", 100,
                           {"tables": len(tables), "columns": len(columns)})
