"""DBMS 테이블/컬럼 보강 — LLM description 요약 + 임베딩 벡터라이징.

원본 위치 (이전):
  - `analyzer/strategy/dbms/pipeline/phase_summary.py` 의
    `_summarize_table_metadata`, `_summarize_table`, `_run_table_summary`
  - `analyzer/strategy/dbms/pipeline/phase_vector.py::run_phase_vector` 의
    OpenAI client 초기화 + tables / columns 루프

책임 (한 도메인 = 한 파일):
  1. 테이블 description 요약 LLM 호출 (rules/dbms/table_summary)
     — `_table_summary_store` 통합 + DDL 메타 병합 + LLM → Neo4j SET
  2. :Table / :Column 임베딩 벡터라이징
     — analyzer/common/llm/embedding 의 strategy-agnostic 빌더 호출

정책 정렬:
  - 테이블 요약 LLM 결과가 dict 가 아니면 fail-fast (silent skip 금지).
  - api_key 누락은 fail-fast (service 레이어 검증 누락 사유).
  - 임베딩 호출 실패는 fail-fast (analyzer/common/llm/embedding 가 errlog + raise).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Tuple

from openai import AsyncOpenAI

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.common.pipeline.embedding import vectorize_columns, vectorize_tables
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.dbms.common.cypher.graph_queries import (
    build_column_description_query,
    build_table_description_query,
)
from analyzer.strategy.dbms.common.models.node_models import FileAnalysisContext, FileStatus
from client.embedding_client import EmbeddingClient
from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.llm_rule_runner import prompts
from util.stream_emitter import emit_message, emit_phase_event

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Description LLM 호출
# ═══════════════════════════════════════════════════════════════════════════


def invoke_table_summary(
    table_name: str,
    table_sentences: List[str],
    column_sentences: Dict[str, List[str]],
    column_metadata: Dict[str, Dict[str, Any]],
    api_key: str,
    locale: str,
) -> Dict[str, Any]:
    """rules/dbms/table_summary.yaml 호출."""
    return prompts.execute_raw(
        "dbms", "table_summary",
        {
            "table_name": table_name,
            "table_sentences": table_sentences,
            "column_sentences": column_sentences,
            "column_metadata": column_metadata,
            "locale": locale,
        },
        api_key,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 단일 테이블 요약 → Cypher 쿼리 변환
# ═══════════════════════════════════════════════════════════════════════════


async def summarize_table(
    table_key: Tuple[str, str],
    data: Dict[str, Any],
    ddl_table_metadata: Dict[Tuple[str, str], Dict[str, Any]],
    dbms: str,
    api_key: str,
    locale: str,
) -> List[Dict[str, Any]]:
    """단일 테이블의 요약 LLM 호출 + 결과 → :Table/:Column SET 쿼리 리스트."""
    schema_key, name_key = table_key
    ddl_key = (schema_key.lower(), name_key.lower())
    ddl_meta = ddl_table_metadata.get(ddl_key, {})
    ddl_description = (ddl_meta.get("description") or "").strip()
    ddl_columns = ddl_meta.get("columns") or {}

    summaries = list(data.get("summaries") or [])
    columns_map = data.get("columns") or {}
    column_sentences = {
        entry["name"]: list(entry["summaries"])
        for entry in columns_map.values()
        if entry.get("summaries")
    }

    if ddl_description:
        summaries.insert(0, f"[DDL 메타데이터] {ddl_description}")
    for col_name, ddl_col in ddl_columns.items():
        ddl_col_desc = (ddl_col.get("description") or "").strip()
        if ddl_col_desc and col_name not in column_sentences:
            column_sentences[col_name] = [f"[DDL 메타데이터] {ddl_col_desc}"]
        elif ddl_col_desc and col_name in column_sentences:
            column_sentences[col_name].insert(0, f"[DDL 메타데이터] {ddl_col_desc}")

    if not summaries and not column_sentences:
        return []

    table_display = f"{schema_key}.{name_key}" if schema_key else name_key
    column_metadata = _build_column_metadata(columns_map, ddl_columns)

    result = await asyncio.to_thread(
        invoke_table_summary,
        table_display, summaries, column_sentences, column_metadata, api_key, locale,
    )
    if not isinstance(result, dict):
        logger.error(
            "[TABLE_SUMMARY_LLM] 결과 dict 가 아님 | table=%s.%s type=%s",
            schema_key, name_key, type(result).__name__,
        )
        raise RuntimeError(f"테이블 요약 결과 오류 ({schema_key}.{name_key})")

    queries: List[Dict[str, Any]] = []
    llm_table_desc = (result.get("tableDescription") or "").strip()
    if llm_table_desc:
        queries.append(build_table_description_query(
            schema=schema_key, name=name_key, db=dbms, llm_desc=llm_table_desc,
        ))
    for column_info in result.get("columns") or []:
        column_name = (column_info.get("name") or "").strip()
        llm_column_desc = (column_info.get("description") or "").strip()
        if not column_name or not llm_column_desc:
            continue
        queries.append(build_column_description_query(
            schema=schema_key, table_name=name_key, column_name=column_name,
            llm_desc=llm_column_desc,
        ))
    return queries


def _build_column_metadata(
    columns_map: Dict[str, Dict[str, Any]],
    ddl_columns: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """columns_map(처리기 누적) + ddl_columns 를 병합한 컬럼 메타 dict."""
    column_metadata: Dict[str, Dict[str, Any]] = {}
    for entry in columns_map.values():
        col_name = entry["name"]
        ddl_col = ddl_columns.get(col_name, {})
        nullable_val = entry.get("nullable")
        column_metadata[col_name] = {
            "dtype": entry.get("dtype") or ddl_col.get("dtype") or "",
            "nullable": (
                bool(nullable_val) if nullable_val is not None
                else ddl_col.get("nullable", True)
            ),
            "examples": sorted(list(entry.get("examples") or []))[:5],
        }
    for col_name, ddl_col in ddl_columns.items():
        if col_name not in column_metadata:
            column_metadata[col_name] = {
                "dtype": ddl_col.get("dtype") or "",
                "nullable": ddl_col.get("nullable", True),
                "examples": [],
            }
    return column_metadata


# ═══════════════════════════════════════════════════════════════════════════
# Description 메인 — 모든 PH2_OK 파일의 _table_summary_store 통합 → 테이블별 요약
# ═══════════════════════════════════════════════════════════════════════════


def _gather_table_stores(
    contexts: List[FileAnalysisContext],
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]], str]:
    """모든 PH2_OK 파일의 ``_table_summary_store`` + ``_ddl_table_metadata`` + ``dbms`` 통합."""
    all_table_stores: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_ddl_metadata: Dict[Tuple[str, str], Dict[str, Any]] = {}
    dbms_name = ""
    for ctx in contexts:
        if ctx.status != FileStatus.PH2_OK or not ctx.processor:
            continue
        proc = ctx.processor
        store = getattr(proc, "_table_summary_store", None) or {}
        for table_key, data in store.items():
            if table_key not in all_table_stores:
                all_table_stores[table_key] = {"summaries": set(), "columns": {}}
            all_table_stores[table_key]["summaries"].update(data.get("summaries") or set())
            for col_name, col_data in (data.get("columns") or {}).items():
                if col_name not in all_table_stores[table_key]["columns"]:
                    all_table_stores[table_key]["columns"][col_name] = col_data.copy()
                else:
                    all_table_stores[table_key]["columns"][col_name]["summaries"].update(
                        col_data.get("summaries") or set()
                    )
        ddl_meta = getattr(proc, "_ddl_table_metadata", None) or {}
        if ddl_meta:
            all_ddl_metadata.update(ddl_meta)
        if not dbms_name and getattr(proc, "dbms", ""):
            dbms_name = proc.dbms
    return all_table_stores, all_ddl_metadata, dbms_name


async def run_table_description_enrichment(
    *,
    client: Neo4jClient,
    contexts: List[FileAnalysisContext],
    api_key: str,
    locale: str,
) -> AsyncGenerator[bytes, None]:
    """모든 PH2_OK 파일의 테이블 description 요약 + Neo4j 반영 (continuous)."""
    all_table_stores, all_ddl_metadata, dbms_name = _gather_table_stores(contexts)
    if not all_table_stores:
        yield emit_phase_event(
            2, "AI 분석", "in_progress", 100,
            {"step": "테이블 요약 완료", "tables": 0},
        )
        return

    total = len(all_table_stores)
    yield emit_phase_event(
        2, "AI 분석", "in_progress", 85,
        {"step": "테이블/컬럼 설명 요약", "total": total},
    )

    tasks = [
        asyncio.create_task(summarize_table(
            table_key,
            {"summaries": list(data["summaries"]), "columns": data["columns"]},
            all_ddl_metadata, dbms_name, api_key, locale,
        ))
        for table_key, data in all_table_stores.items()
    ]

    completed = 0
    total_queries = 0
    for coro in asyncio.as_completed(tasks):
        completed += 1
        queries = await coro
        if queries:
            total_queries += len(queries)
            yield emit_message(f"   ✓ [{completed}/{total}] 테이블 요약 완료")
            async for chunk in write_to_graph(client, queries, "DBMS_PHASE2:TABLE_SUMMARY"):
                yield chunk
        yield emit_phase_event(
            2, "AI 분석", "in_progress", 85 + int(completed / total * 15),
            {"step": "테이블/컬럼 설명 요약", "done": completed, "total": total},
        )

    # 처리 완료된 store 정리 (메모리 절약)
    for ctx in contexts:
        proc = ctx.processor
        if proc is not None and hasattr(proc, "_table_summary_store"):
            proc._table_summary_store.clear()

    yield emit_phase_event(
        2, "AI 분석", "in_progress", 100,
        {"step": "테이블/컬럼 설명 요약 완료", "tables": total, "queries": total_queries},
    )


# ═══════════════════════════════════════════════════════════════════════════
# 임베딩 벡터라이징 — :Table / :Column 노드
# ═══════════════════════════════════════════════════════════════════════════


def _build_embedding_client(api_key: str) -> EmbeddingClient:
    """OpenAI client → EmbeddingClient 래핑.

    LLM_API_BASE 가 지정되어 있으면 그 base_url 사용 (자체 임베딩 서버 지원).
    """
    base_url = settings.llm.api_base or None
    openai_client = (
        AsyncOpenAI(api_key=api_key, base_url=base_url)
        if base_url
        else AsyncOpenAI(api_key=api_key)
    )
    return EmbeddingClient(openai_client)


async def vectorize_dbms_table_column(
    *,
    client: Neo4jClient,
    api_key: str,
    stats: AnalysisStats,
    phase_num: int = 4,
) -> AsyncGenerator[bytes, None]:
    """:Table 와 :Column 노드 벡터라이징 (embedding=NULL 만).

    `stats.tables_vectorized` / `stats.columns_vectorized` 를 누적 갱신.

    emit_phase_event 는 `in_progress` 만 yield. `completed` 는 호출자(dbms_pipeline) 가
    감싸 emit (Framework 패턴 통일).

    Yields:
        emit_message / emit_phase_event(in_progress) 바이트.
    """
    if not api_key:
        logger.error("[PHASE:4:VECTOR] api_key 누락 — pipeline 진입 시점 검증 누락")
        raise RuntimeError("Phase4(VECTOR) 진입 시 api_key 누락 — service 레이어 검증 필요")

    embedder = _build_embedding_client(api_key)

    # Table 벡터라이징 — 진행률 emit (25% → 75%) + stats.tables_vectorized 누적
    async for evt in vectorize_tables(
        client, embedder,
        stats=stats,
        batch_size=32, phase_num=phase_num,
        progress_start=25, progress_end=75,
    ):
        yield evt

    # Column 벡터라이징 — 카운트 반환 + stats 누적
    column_count = await vectorize_columns(client, embedder, batch_size=32)
    stats.columns_vectorized += column_count

    yield emit_message(f"   ✅ 벡터라이징 완료: 테이블 {stats.tables_vectorized}개, 컬럼 {column_count}개")


__all__ = [
    "invoke_table_summary",
    "summarize_table",
    "run_table_description_enrichment",
    "vectorize_dbms_table_column",
]
