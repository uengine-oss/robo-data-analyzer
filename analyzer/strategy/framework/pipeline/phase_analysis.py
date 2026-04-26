"""Phase 3a: 컨텍스트 수집 + 코드 분석 LLM 배치 — thin orchestrator.

비즈니스 로직 분리 (Stage 5):
  - 컨텍스트 수집     → `framework/analysis/pre_analysis.py`
  - tableInsights 누적 → `framework/enrichment/table_description_enrichment.py`
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.strategy.framework.analysis.ast_analyzer import analyze_and_enrich
from analyzer.strategy.framework.analysis.pre_analysis import (
    collect_analysis_context as _collect_context,
)
from analyzer.strategy.framework.common.cypher.graph_queries import (
    build_analysis_result_queries,
    build_uml_dependency_queries,
)
from analyzer.strategy.framework.common.domain.ontology import get_ontology
from analyzer.strategy.framework.enrichment.table_description_enrichment import (
    accumulate_table_insights,
)
from config.settings import settings
from util.stream_emitter import PhaseTracker

logger = logging.getLogger(__name__)


async def collect_analysis_context(ctx: Any) -> dict:
    """phase 진입점에서 호출 — pre_analysis 모듈 wrapper (FrameworkPhaseContext 패턴)."""
    return await _collect_context(ctx.state, ctx.client, ctx.orchestrator)


async def run_code_analysis(
    ctx: Any,  # FrameworkPhaseContext
    tracker: PhaseTracker,
    summary_ctx: dict,
) -> AsyncGenerator[bytes, None]:
    """메서드별 코드 분석 (summary + stereotype + dependency + calls + variables + tableInsights)."""
    st = ctx.state
    client = ctx.client
    function_facts_list = st.function_facts_list or []
    reads_writes = st.reads_writes or []
    api_key = settings.llm.api_key
    locale = settings.locale

    total_batches = max(1, (len(function_facts_list) + 19) // 20)
    yield tracker.begin_step(0, total_batches)

    analysis_by_function: dict = {}
    table_map: dict[str, dict] = {}
    # 컨텍스트 테이블 = Phase 2 에서 식별된 테이블 (소문자). 그 외는 LLM 환각으로 간주하여 드롭.
    allowed_tables = {rw.table_name.strip().lower() for rw in reads_writes if rw.table_name}
    allowed_tables |= {
        e.table_name.strip().lower()
        for e in (st.function_table_edges or [])
        if e.table_name
    }

    for analysis_batch in analyze_and_enrich(
        function_facts_list,
        st.refer_to_edges or [],
        reads_writes,
        use_llm=bool(api_key),
        api_key=api_key,
        locale=locale,
        parallel=True,
        module_index=st.module_index or {},
        sql_index=st.sql_index or {},
        config_index=st.config_index,
        calls_edges=st.calls_edges or [],
        variable_facts_list=st.variable_facts_list or [],
        lang=st.lang,
        table_sample_data=summary_ctx.get("table_sample_data") or None,
        table_sample_data_by_function=summary_ctx.get("table_sample_data_by_function") or None,
        constants_context=summary_ctx.get("constants_context") or None,
        referenced_vars_by_function=summary_ctx.get("referenced_vars_by_function") or None,
        codebase_conventions=summary_ctx.get("codebase_conventions") or None,
        reads_writes_by_function=summary_ctx.get("reads_writes_by_function") or None,
    ):
        analysis_by_function.update(analysis_batch)
        yield tracker.tick_quiet()

        queries = list(build_analysis_result_queries(analysis_batch, lang=st.lang))

        if get_ontology().is_oop(st.lang):
            queries.extend(build_uml_dependency_queries(
                analysis_batch, function_facts_list, st.module_index or {},
            ))

        accumulate_table_insights(analysis_batch, table_map, allowed_tables)

        if queries:
            async for chunk in write_to_graph(client, queries, "PHASE3:ANALYSIS"):
                yield chunk

    st.analysis_by_function = analysis_by_function
    st.table_insights_map = table_map
    yield tracker.end_step(f"메서드 {len(analysis_by_function)}개 분석 완료")
    if table_map:
        logger.info(
            "[PHASE:3:TABLE_INSIGHTS] 누적 완료 | tables=%d columns=%d "
            "(Table Description 보강에서 일괄 요약 후 MATCH 반영)",
            len(table_map), sum(len(t.get("columns", {})) for t in table_map.values()),
        )
