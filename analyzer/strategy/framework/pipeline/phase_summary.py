"""Phase 3b: Module 요약 + Actor 통합 + Table Description 보강 — thin orchestrator.

비즈니스 로직 분리 (Stage 5):
  - Actor 통합 (1차 모듈 내 + 2차 모듈 간 LLM)
        → `framework/analysis/actor_unifier.py`
  - Table description LLM 보강
        → `framework/enrichment/table_description_enrichment.py`

본 phase 는 순서 제어 + tracker step emit + 다른 모듈 호출만 수행.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.strategy.framework.analysis.actor_unifier import (
    apply_module_unified_actors,
    merge_cross_module_actors,
)
from analyzer.strategy.framework.analysis.ast_analyzer import run_module_summary
from analyzer.strategy.framework.common.cypher.graph_queries import (
    build_actor_queries,
    build_module_summary_queries,
)
from analyzer.strategy.framework.enrichment.table_description_enrichment import (
    run_table_description_enrichment,
)
from config.settings import settings
from util.stream_emitter import PhaseTracker

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
        async for chunk in run_table_description_enrichment(
            client=client,
            insights_map=st.table_insights_map or {},
            api_key=api_key,
            locale=locale,
        ):
            yield chunk
    yield tracker.end_step("테이블 설명 보강 완료")
