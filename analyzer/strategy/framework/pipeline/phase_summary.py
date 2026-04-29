"""Phase 3b: Module 요약 + Actor 통합 + Table Description 보강 — thin orchestrator.

비즈니스 로직 분리 (Stage 5):
  - Actor 통합 (1차 모듈 내 + 2차 모듈 간 LLM)
        → `framework/analysis/actor_unifier.py`
  - Table description LLM 보강
        → `analyzer/common/pipeline/table_description_enrichment.py` (오케스트레이션)
        → 데이터 변환은 본 phase 내 _build_framework_table_inputs 등 헬퍼

본 phase 는 순서 제어 + tracker step emit + 다른 모듈 호출만 수행.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.common.pipeline.table_description_enrichment import run_table_enrichment
from analyzer.strategy.framework.analysis.actor_unifier import (
    apply_module_unified_actors,
    merge_cross_module_actors,
)
from analyzer.strategy.framework.analysis.ast_analyzer import run_module_summary
from analyzer.strategy.framework.common.cypher.graph_queries import (
    build_actor_queries,
    build_column_description_update_queries,
    build_module_summary_queries,
    build_table_description_enrichment_query,
    build_table_description_update_queries,
)
from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.llm_rule_runner import prompts
from util.stream_emitter import PhaseTracker, emit_message

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Step 3: Module 분석 (Field 분석은 비활성화 상태)
# ═══════════════════════════════════════════════════════════════════════════


async def run_field_and_module_analysis(
    ctx: Any,  # FrameworkPhaseContext
    tracker: PhaseTracker,
) -> AsyncGenerator[bytes, None]:
    """Field 분석 (비활성화) + Module 분석."""
    st = ctx.state
    client = ctx.client
    api_key = settings.llm.api_key
    locale = settings.locale
    variable_facts_list = st.variable_facts_list or []
    module_index = st.module_index or {}
    function_facts_list = st.function_facts_list or []
    analysis_by_function = st.analysis_by_function or {}

    summary_by_variable: dict = {}
    yield tracker.end_step("필드 분석 비활성화")

    module_total = max(1, len(module_index))
    yield tracker.begin_step(2, module_total)
    summary_by_module: dict = {}
    for module_batch in run_module_summary(
        module_index, analysis_by_function, summary_by_variable,
        function_facts_list, variable_facts_list,
        api_key=api_key, locale=locale,
    ):
        summary_by_module.update(module_batch)
        yield tracker.tick_quiet(len(module_batch))
        queries = build_module_summary_queries(module_batch, lang=st.lang)
        if queries:
            async for chunk in write_to_graph(client, queries, "PHASE3:MODULE_ANALYSIS"):
                yield chunk

    yield tracker.end_step(f"클래스 {len(summary_by_module)}개 분석 완료")

    st.summary_by_variable = summary_by_variable
    st.summary_by_module = summary_by_module


# ═══════════════════════════════════════════════════════════════════════════
# Step 4: Actor 통합 + Table Description 보강
# ═══════════════════════════════════════════════════════════════════════════


async def run_actor_and_table_enrichment(
    ctx: Any,  # FrameworkPhaseContext
    tracker: PhaseTracker,
) -> AsyncGenerator[bytes, None]:
    """Actor 동의어 통합 + Table Description LLM 보강."""
    st = ctx.state
    client = ctx.client
    api_key = settings.llm.api_key
    locale = settings.locale
    analysis_by_function = st.analysis_by_function or {}
    summary_by_module = st.summary_by_module or {}
    function_facts_list = st.function_facts_list or []

    # Actor 통합 — 1차 (모듈 내 결정론) + 2차 (모듈 간 LLM)
    replaced_count = apply_module_unified_actors(
        analysis_by_function, summary_by_module, function_facts_list,
    )
    if replaced_count:
        logger.info("[PHASE:3:ACTOR_MERGE] 1차 통합 (모듈 내) | replaced=%d", replaced_count)

    if api_key:
        merge_map = await merge_cross_module_actors(
            analysis_by_function, summary_by_module, api_key, locale,
        )
        if merge_map:
            logger.info("[PHASE:3:ACTOR_MERGE] 2차 통합 (모듈 간) | merge_map=%s", merge_map)

    # Actor 노드 생성 + ROLE 관계 반영
    actor_queries = build_actor_queries(analysis_by_function)
    if actor_queries:
        async for chunk in write_to_graph(client, actor_queries, "PHASE3:ACTORS"):
            yield chunk
        actor_count = len({d.get("actor") for d in analysis_by_function.values() if d.get("actor")})
        logger.info("[PHASE:3:ACTOR_MERGE] Actor 노드 생성 | actors=%d", actor_count)

    # Table description LLM 보강
    yield tracker.begin_step(3, 1)
    if api_key and analysis_by_function:
        async for chunk in _run_framework_table_enrichment(
            client=client,
            insights_map=st.table_insights_map or {},
            api_key=api_key,
            locale=locale,
        ):
            yield chunk
    yield tracker.end_step("테이블 설명 보강 완료")


# ═══════════════════════════════════════════════════════════════════════════
# Table description 보강 — Framework 전용 데이터 변환 + common.run_table_enrichment
# ═══════════════════════════════════════════════════════════════════════════


def _build_framework_payload(
    table_info: Dict[str, Any],
    insights_map: Dict[str, Dict[str, Any]],
    locale: str,
) -> Dict[str, Any]:
    """LLM payload (table_name, sentences, col_sentences, col_metadata, locale) 구성."""
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

    return {
        "table_name": table_display,
        "table_sentences": sentences,
        "column_sentences": col_sentences,
        "column_metadata": col_metadata,
        "locale": locale,
    }


async def _run_framework_table_enrichment(
    *,
    client: Neo4jClient,
    insights_map: Dict[str, Dict[str, Any]],
    api_key: str,
    locale: str,
) -> AsyncGenerator[bytes, None]:
    """Framework 테이블 description 보강: Neo4j 에서 데이터 수집 + common.run_table_enrichment."""
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

    # canonical TableInput 으로 변환
    table_inputs: List[Dict[str, Any]] = []
    for t in tables_with_summaries:
        table_inputs.append({
            "extra": {
                "db": t.get("db", ""),
                "schema": t.get("schema", ""),
                "name": t.get("name", ""),
            },
            "payload": _build_framework_payload(t, insights_map, locale),
        })

    async def _invoke(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        result = await asyncio.to_thread(
            prompts.table_summary,
            table_name=payload["table_name"],
            table_sentences=payload["table_sentences"],
            column_sentences=payload["column_sentences"],
            column_metadata=payload["column_metadata"],
            locale=payload["locale"],
            api_key=api_key,
        )
        if not (isinstance(result, dict) and result.get("tableDescription")):
            logger.error(
                "[TABLE_ENRICHER] LLM 결과 누락 — table=%s result_type=%s",
                payload["table_name"], type(result).__name__,
            )
            return None  # noqa: SILENT_FAIL — 한 테이블 실패가 전체 보강 막지 않음
        return result

    def _build_queries(extra: Dict[str, Any], result: Dict[str, Any]) -> List[Dict[str, Any]]:
        item = {
            "db": extra.get("db", ""),
            "schema": extra.get("schema", ""),
            "name": extra.get("name", ""),
            "enriched_description": result["tableDescription"],
            "columns": result.get("columns") or [],
        }
        queries = build_table_description_update_queries([item])
        if item.get("columns"):
            queries.extend(build_column_description_update_queries(
                item["db"], item["schema"], item["name"], item["columns"],
            ))
        return queries

    async for evt in run_table_enrichment(
        client=client,
        table_inputs=table_inputs,
        invoke_llm=_invoke,
        build_queries=_build_queries,
        log_tag="PHASE3:TABLE_ENRICHMENT",
        phase_num=3, progress_start=0, progress_end=100,  # tracker 가 step 단위로 진행률 관리
    ):
        yield evt
