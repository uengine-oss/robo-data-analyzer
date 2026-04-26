"""Framework 테이블 description 보강 — Insights 누적 + LLM 보강.

원본 위치 (이전):
  - `analyzer/strategy/framework/pipeline/phase_analysis.py::_accumulate_table_insights`
  - `analyzer/strategy/framework/pipeline/phase_summary.py::run_actor_and_table_enrichment`
    안에 있던 `_enrich_one_table` 클로저 + ThreadPoolExecutor 루프 + Neo4j 반영.

책임 (한 도메인 = 한 파일):
  1. Phase 3 LLM code_analysis 결과의 tableInsights 누적 (`accumulate_table_insights`)
     — 함수별로 LLM 이 추출한 테이블 사용 정보를 함수→테이블 맵으로 모음
  2. Phase 3 후처리 — 모은 insights + Neo4j 메타로 테이블 description 보강 LLM 호출
     (`run_table_description_enrichment`)
     — :Table.enriched_description / :Column.description SET

정책 정렬:
  - LLM 환각 방지: 컨텍스트 외 테이블(allowed_tables 미포함) 은 errlog + 드롭.
  - LLM 결과 dict 가 아니거나 tableDescription 누락 시 errlog + None 반환 (skip).
    부분 실패가 fatal 은 아님 — 다른 테이블 보강은 계속.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.strategy.framework.common.cypher.graph_queries import (
    build_column_description_update_queries,
    build_table_description_enrichment_query,
    build_table_description_update_queries,
)
from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.llm_rule_runner import prompts
from util.stream_emitter import emit_message

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 — tableInsights 누적 (LLM code_analysis 결과 → 함수→테이블 맵)
# ═══════════════════════════════════════════════════════════════════════════


def accumulate_table_insights(
    analysis_batch: Dict[str, Any],
    table_map: Dict[str, Dict[str, Any]],
    allowed_tables: Set[str],
) -> None:
    """tableInsights 를 ``table_map`` 에 누적 (in-place).

    Args:
        analysis_batch: ``{function_id: analysis_dict}`` (LLM code_analysis 배치 결과)
        table_map:      누적 대상 dict — ``{table_name_lower: {descriptions, columns, source_functions}}``
        allowed_tables: Phase 2 에서 식별된 컨텍스트 테이블명 (소문자). 그 외는 드롭.
    """
    for function_id, analysis in analysis_batch.items():
        for insight in (analysis.get("tableInsights") or []):
            tbl_name = (insight.get("tableName") or "").strip().lower()
            if not tbl_name:
                continue
            if tbl_name not in allowed_tables:
                logger.warning(
                    "[TABLE_INSIGHTS] 컨텍스트 외 테이블 드롭 | function_id=%s table=%s",
                    function_id, tbl_name,
                )
                continue

            tbl_desc = (insight.get("description") or "").strip()
            slot = table_map.setdefault(
                tbl_name,
                {"descriptions": [], "columns": {}, "source_functions": []},
            )
            if tbl_desc:
                slot["descriptions"].append(tbl_desc)
            if function_id:
                slot["source_functions"].append(function_id)
            for col in (insight.get("columns") or []):
                col_name = (col.get("name") or "").strip()
                col_desc = (col.get("description") or "").strip()
                if not col_name:
                    continue
                col_slot = slot["columns"].setdefault(col_name, {"descriptions": []})
                if col_desc:
                    col_slot["descriptions"].append(col_desc)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 후처리 — 단일 테이블 LLM 호출 → enrichment dict
# ═══════════════════════════════════════════════════════════════════════════


def _build_table_input(
    table_info: Dict[str, Any],
    insights_map: Dict[str, Dict[str, Any]],
) -> tuple[str, list[str], dict, dict]:
    """LLM 입력 (table_display, sentences, col_sentences, col_metadata) 구성."""
    name = table_info.get("name", "")
    schema = table_info.get("schema", "")
    current_desc = table_info.get("current_description") or ""
    method_summaries = table_info.get("method_summaries") or []
    columns_meta = table_info.get("columns") or []
    table_display = f"{schema}.{name}" if schema else name

    sentences = [f"[메서드 요약] {s}" for s in method_summaries[:20]]
    if current_desc:
        sentences.insert(0, f"[기존 설명] {current_desc}")

    insight_slot = insights_map.get(name.lower()) or insights_map.get(name) or {}
    for desc in (insight_slot.get("descriptions") or [])[:10]:
        if desc:
            sentences.append(f"[함수별 테이블 역할] {desc}")

    col_metadata: Dict[str, Dict[str, Any]] = {}
    col_sentences: Dict[str, List[str]] = {}
    for col in columns_meta:
        col_name = col.get("name", "")
        if not col_name:
            continue
        col_metadata[col_name] = {
            "dtype": col.get("dtype", ""),
            "nullable": col.get("nullable"),
            "is_primary_key": col.get("is_primary_key"),
            "description": col.get("description", ""),
        }
    for col_name, col_slot in (insight_slot.get("columns") or {}).items():
        descs = [d for d in (col_slot.get("descriptions") or []) if d]
        if descs:
            col_sentences[col_name] = descs[:10]

    return table_display, sentences, col_sentences, col_metadata


def enrich_one_table(
    table_info: Dict[str, Any],
    insights_map: Dict[str, Dict[str, Any]],
    api_key: str,
    locale: str,
) -> Optional[Dict[str, Any]]:
    """단일 테이블에 대해 ``rules/dbms/table_summary`` LLM 호출.

    Returns:
        ``{db, schema, name, enriched_description, columns}`` 또는 None (LLM 결과 누락).
    """
    table_display, sentences, col_sentences, col_metadata = _build_table_input(
        table_info, insights_map,
    )
    result = prompts.table_summary(
        table_name=table_display,
        table_sentences=sentences,
        column_sentences=col_sentences,
        column_metadata=col_metadata,
        locale=locale,
        api_key=api_key,
    )
    if not (isinstance(result, dict) and result.get("tableDescription")):
        logger.error(
            "[TABLE_ENRICHER] LLM 결과 누락 — table=%s result_type=%s",
            table_display, type(result).__name__,
        )
        return None  # noqa: SILENT_FAIL — 한 테이블 LLM 실패가 다른 테이블 보강을 막지 않도록 skip
    return {
        "db": table_info.get("db", ""),
        "schema": table_info.get("schema", ""),
        "name": table_info.get("name", ""),
        "enriched_description": result["tableDescription"],
        "columns": result.get("columns") or [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Phase 3 후처리 메인 — 모든 보강 대상 테이블 → 병렬 LLM + Neo4j 반영
# ═══════════════════════════════════════════════════════════════════════════


async def run_table_description_enrichment(
    *,
    client: Neo4jClient,
    insights_map: Dict[str, Dict[str, Any]],
    api_key: str,
    locale: str,
) -> AsyncGenerator[bytes, None]:
    """보강 대상 테이블 전수 → LLM 병렬 호출 → :Table/:Column SET (continuous).

    Yields:
        emit_message + write_to_graph 의 bytes.
    호출자가 PhaseTracker step 진입/종료 emit 은 별도로 관리.
    """
    if not api_key:
        return
    enrichment_query = build_table_description_enrichment_query()
    table_results = await client.execute_queries([enrichment_query])
    tables_with_summaries: List[Dict[str, Any]] = (
        table_results[0] if table_results and table_results[0] else []
    )
    if not tables_with_summaries:
        return

    yield emit_message(f"  ├─ {len(tables_with_summaries)}개 테이블 LLM 설명 보강 중...")

    workers = max(1, settings.concurrency.max_concurrency)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(enrich_one_table, t, insights_map, api_key, locale): t
            for t in tables_with_summaries
        }
        for future in as_completed(futures):
            item = future.result()
            if not item:
                continue
            queries = build_table_description_update_queries([item])
            if item.get("columns"):
                queries.extend(build_column_description_update_queries(
                    item.get("db", ""),
                    item.get("schema", ""),
                    item.get("name", ""),
                    item["columns"],
                ))
            if queries:
                async for chunk in write_to_graph(client, queries, "PHASE3:TABLE_ENRICHMENT"):
                    yield chunk


__all__ = [
    "accumulate_table_insights",
    "enrich_one_table",
    "run_table_description_enrichment",
]
