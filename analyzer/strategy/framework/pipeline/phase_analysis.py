"""Phase 3a: 컨텍스트 수집 + 코드 분석 LLM 배치 — thin orchestrator.

비즈니스 로직 분리 (Stage 5):
  - 컨텍스트 수집     → `framework/analysis/pre_analysis.py`
  - tableInsights 누적 → `framework/enrichment/table_description_enrichment.py`
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, Set

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
from config.settings import settings
from util.stream_emitter import PhaseTracker

logger = logging.getLogger(__name__)


def accumulate_table_insights(
    analysis_batch: Dict[str, Any],
    table_map: Dict[str, Dict[str, Any]],
    allowed_tables: Set[str],
) -> None:
    """tableInsights 를 ``table_map`` 에 누적 (in-place).

    Args:
        analysis_batch: ``{function_id: analysis_dict}`` (LLM code_analysis 배치 결과)
        table_map:      누적 대상 dict — ``{table_name_lower: {descriptions, columns, source_functions}}``
        allowed_tables: Phase 2 에서 식별된 컨텍스트 테이블명 (소문자). 그 외는 errlog 후 드롭.
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
        ddl_context_by_function=summary_ctx.get("ddl_context_by_function") or None,
        constants_context=summary_ctx.get("constants_context") or None,
        referenced_vars_by_function=summary_ctx.get("referenced_vars_by_function") or None,
        codebase_pattern=summary_ctx.get("codebase_pattern") or None,
        reads_writes_by_function=summary_ctx.get("reads_writes_by_function") or None,
    ):
        analysis_by_function.update(analysis_batch)
        msg, evt = tracker.tick()
        yield msg
        yield evt

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
