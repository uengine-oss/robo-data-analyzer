"""Phase 3: 코드 분석 + 보강.

4단계:
1. 컨텍스트 수집 (상수, 참조변수, 테이블 샘플)
2. 메서드 분석 (LLM 배치: summary + stereotype + dependency + calls + variables + tableInsights)
3. Field + Module 분석
4. Actor 병합 + Table Description 보강
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, AsyncGenerator, Dict, List, Optional

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from config.settings import settings
from util.ndjson_stream import emit_message, emit_data, PhaseTracker
from analyzer.strategy.framework.linking.code_analyzer import (
    analyze_and_enrich,
    run_module_summary,
)
from analyzer.strategy.framework.graph.queries import (
    build_analysis_result_queries,
    build_uml_dependency_queries,
    build_module_summary_queries,
    build_table_description_enrichment_query,
    build_table_description_update_queries,
    build_actor_queries,
)
from analyzer.common.graph_writer import run_graph_queries
from analyzer.strategy.framework.pipeline.progress import write_to_graph

logger = logging.getLogger(__name__)


async def run_phase_enrichment(
    pipeline: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 코드 분석 + 보강."""
    st = pipeline._pipeline_state

    tracker = PhaseTracker(2, "코드 분석 + 보강", total_phases=3)
    tracker.define_steps([
        ("코드 분석", 35),
        ("Field 분석", 15),
        ("Module 분석", 25),
        ("Table Description 보강", 25),
    ])

    # Step 1: 컨텍스트 수집
    summary_ctx = await _collect_analysis_context(st, client, orchestrator)

    # Step 2: 메서드 분석
    async for chunk in _run_code_analysis(st, client, tracker, summary_ctx):
        yield chunk

    # Step 3: Field + Module 분석
    async for chunk in _run_field_and_module_analysis(st, client, tracker):
        yield chunk

    # Step 4: Actor 병합 + Table Description 보강
    async for chunk in _run_actor_and_table_enrichment(st, client, tracker, orchestrator):
        yield chunk

    yield emit_message(
        f"✅ Phase 3 완료 | 메서드분석 {len(st.analysis_by_function)}개, "
        f"필드분석 {len(st.summary_by_variable)}개, 클래스분석 {len(st.summary_by_module)}개"
    )

    # catalog 메타데이터 보강 트리거
    datasource_name = getattr(orchestrator, "datasource_name", "")
    if datasource_name:
        try:
            import aiohttp
            catalog_url = os.environ.get("CATALOG_SERVICE_URL", "http://localhost:5503")
            async with aiohttp.ClientSession() as _session:
                await _session.post(
                    f"{catalog_url}/robo/schema/enrich-metadata",
                    json={"datasource_name": datasource_name},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            logger.info("[PHASE:3:CATALOG] 메타데이터 보강 트리거 전송 완료")
        except Exception as e:
            logger.error("[PHASE:3:CATALOG] 보강 트리거 실패 | error=%s", e)


# ═══════════════════════════════════════════════════════════════════════
# Step 1: 컨텍스트 수집
# ═══════════════════════════════════════════════════════════════════════

async def _collect_analysis_context(st, client, orchestrator) -> dict:
    """상수, 참조변수, 테이블 샘플 컨텍스트 수집."""
    ctx: dict = {"constants_context": "", "referenced_vars_by_function": {}, "table_sample_data": ""}

    # 상수 컨텍스트
    from analyzer.common.constant_context import fetch_constants_context
    ctx["constants_context"] = await fetch_constants_context(client)

    # 참조 변수 + INIT_BY 초기화 출처 컨텍스트
    # 함수가 참조하는 변수와, 그 변수가 어떤 상수/변수로 초기화되었는지를 쌍으로 수집한다.
    try:
        ref_results = await client.execute_queries([
            "MATCH (f:FUNCTION)-[:REFERENCES]->(v) "
            "OPTIONAL MATCH (v)-[:INIT_BY]->(src) "
            "RETURN f.function_id AS fid, v.code_text AS code, "
            "       src.name AS src_name, src.code_text AS src_code"
        ])
        init_by_count = 0
        if ref_results and ref_results[0]:
            for row in ref_results[0]:
                fid = row.get("fid", "")
                code = (row.get("code") or "").strip()
                if not (fid and code):
                    continue
                src_code = (row.get("src_code") or "").strip()
                if src_code:
                    entry = f"{code}\n  \u21b3 \ucd08\uae30\ud654 \ucd9c\ucc98: {src_code}"
                    init_by_count += 1
                else:
                    entry = code
                ctx["referenced_vars_by_function"].setdefault(fid, []).append(entry)
        if ctx["referenced_vars_by_function"]:
            logger.info(
                "[PHASE:3:CONTEXT] \ucc38\uc870 \ubcc0\uc218 \uc218\uc9d1 | functions=%d init_by_pairs=%d",
                len(ctx["referenced_vars_by_function"]), init_by_count,
            )
    except Exception as e:
        logger.error(
            "[PHASE:3:CONTEXT] \ucc38\uc870 \ubcc0\uc218 \uc218\uc9d1 \uc2e4\ud328 (Fail-Fast) | error=%s",
            e, exc_info=True,
        )
        raise

    # 테이블 샘플 데이터
    reads_writes = st.reads_writes or []
    try:
        from analyzer.common.table_context import fetch_table_context
        table_names: list[str] = []
        if reads_writes:
            table_names = list({rw.table_name for rw in reads_writes if rw.table_name})
        if not table_names:
            table_results = await client.execute_queries([
                "MATCH (t:Table) RETURN DISTINCT t.name AS name"
            ])
            table_names = [r["name"] for r in (table_results[0] if table_results and table_results[0] else []) if r.get("name")]
        if table_names:
            datasource_name = getattr(orchestrator, "datasource_name", "")
            ctx["table_sample_data"] = await fetch_table_context(client, table_names, datasource_name)
            logger.info("[PHASE:3:CONTEXT] 테이블 샘플 수집 | tables=%d data_len=%d", len(table_names), len(ctx["table_sample_data"]))
    except Exception as e:
        logger.error("[PHASE:3:CONTEXT] 테이블 샘플 수집 실패 | error=%s", e, exc_info=True)

    return ctx


# ═══════════════════════════════════════════════════════════════════════
# Step 2: 메서드 분석 (코드 분석 LLM 배치)
# ═══════════════════════════════════════════════════════════════════════

async def _run_code_analysis(
    st, client, tracker, summary_ctx,
) -> AsyncGenerator[bytes, None]:
    """메서드별 코드 분석 (summary + stereotype + dependency + calls + variables + tableInsights)."""
    function_facts_list = st.function_facts_list or []
    reads_writes = st.reads_writes or []
    api_key = settings.llm.api_key
    locale = settings.locale

    total_batches = max(1, (len(function_facts_list) + 19) // 20)
    yield tracker.begin_step(0, total_batches)

    analysis_by_function: dict = {}
    table_map: dict[str, dict] = {}
    # 컨텍스트 테이블 = Phase 2 에서 이미 식별된 테이블 (소문자). Phase 3 는 여기 있는 것만 설명 보강.
    allowed_tables = {(rw.table_name or "").strip().lower() for rw in reads_writes if rw.table_name}
    allowed_tables |= {(e.table_name or "").strip().lower() for e in (st.function_table_edges or []) if e.table_name}

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
        constants_context=summary_ctx.get("constants_context") or None,
        referenced_vars_by_function=summary_ctx.get("referenced_vars_by_function") or None,
    ):
        analysis_by_function.update(analysis_batch)
        yield tracker.tick_quiet()

        queries: list[dict[str, Any]] = []

        # 1) 분석 결과 쿼리 (summary, stereotype, dependency 등)
        try:
            queries.extend(build_analysis_result_queries(analysis_batch, lang=st.lang))
        except Exception as e:
            logger.error("[PHASE:3:CODE_ANALYSIS] 쿼리 빌드 실패 | error=%s", e, exc_info=True)

        # 2) UML dependency (OOP only)
        try:
            from analyzer.strategy.framework.common.ontology import get_ontology
            if get_ontology().is_oop(st.lang):
                queries.extend(build_uml_dependency_queries(
                    analysis_batch, function_facts_list, st.module_index or {},
                ))
        except Exception as e:
            logger.error("[PHASE:3:UML] 쿼리 빌드 실패 | error=%s", e, exc_info=True)

        # 3) tableInsights 는 table_map 에 누적 (배치 후 일괄 반영, 컨텍스트 테이블만)
        try:
            _accumulate_table_insights(analysis_batch, table_map, allowed_tables)
        except Exception as e:
            logger.error("[PHASE:3:TABLE_INSIGHTS] 누적 실패 | error=%s", e, exc_info=True)

        try:
            if queries:
                async for chunk in write_to_graph(client, queries, "PHASE3:ANALYSIS"):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:3:CODE_ANALYSIS] Neo4j 반영 실패 | error=%s", e, exc_info=True)

    st.analysis_by_function = analysis_by_function
    st.table_insights_map = table_map
    yield tracker.end_step(f"메서드 {len(analysis_by_function)}개 분석 완료")
    if table_map:
        logger.info(
            "[PHASE:3:TABLE_INSIGHTS] 누적 완료 | tables=%d columns=%d "
            "(Table Description 보강에서 일괄 요약 후 MATCH 반영)",
            len(table_map), sum(len(t.get("columns", {})) for t in table_map.values()),
        )


def _accumulate_table_insights(
    analysis_batch: dict,
    table_map: dict,
    allowed_tables: set[str],
) -> None:
    """tableInsights 를 table_map 에 누적. **컨텍스트 테이블만 허용**.

    ``allowed_tables`` 는 Phase 2 에서 이미 식별되어 Neo4j 에 있는 테이블명 (소문자).
    LLM 이 그 외 테이블을 만들어내면 설계 원칙 ("컨텍스트 테이블만") 위반이므로 드롭하고 경고.
    """
    for _fid, analysis in analysis_batch.items():
        for insight in (analysis.get("tableInsights") or []):
            tbl_name = (insight.get("tableName") or "").strip().lower()
            if not tbl_name:
                continue
            if tbl_name not in allowed_tables:
                logger.warning(
                    "[PHASE:3:TABLE_INSIGHTS] 컨텍스트 외 테이블 드롭 | function_id=%s table=%s",
                    _fid, tbl_name,
                )
                continue

            tbl_desc = (insight.get("description") or "").strip()
            slot = table_map.setdefault(
                tbl_name,
                {"descriptions": [], "columns": {}, "source_functions": []},
            )
            if tbl_desc:
                slot["descriptions"].append(tbl_desc)
            if _fid:
                slot["source_functions"].append(_fid)
            for col in (insight.get("columns") or []):
                col_name = (col.get("name") or "").strip()
                col_desc = (col.get("description") or "").strip()
                if not col_name:
                    continue
                col_slot = slot["columns"].setdefault(col_name, {"descriptions": []})
                if col_desc:
                    col_slot["descriptions"].append(col_desc)




# ═══════════════════════════════════════════════════════════════════════
# Step 3: Field + Module 분석
# ═══════════════════════════════════════════════════════════════════════

async def _run_field_and_module_analysis(st, client, tracker) -> AsyncGenerator[bytes, None]:
    """Field 분석 + Module 분석."""
    api_key = settings.llm.api_key
    locale = settings.locale
    variable_facts_list = st.variable_facts_list or []
    module_index = st.module_index or {}
    function_facts_list = st.function_facts_list or []
    analysis_by_function = st.analysis_by_function or {}

    # ── Field 분석 (비활성화) ──
    # 변수는 Navigator 에 노출하지 않는 정보이고, 현재 입력이 변수 자기 선언 한 줄뿐이라
    # 요약할 가치가 없음. 재개가 필요해지면 code_analysis 에서 참조 관계(REFERENCES)와
    # 사용 맥락을 함께 뱉어주도록 보강해야 한다.
    summary_by_variable: Dict[str, Any] = {}
    yield tracker.end_step("필드 분석 비활성화")

    # ── Module 분석 ──
    module_total = max(1, len(module_index))
    yield tracker.begin_step(2, module_total)
    summary_by_module: Dict[str, Any] = {}
    for module_batch in run_module_summary(
        module_index, analysis_by_function, summary_by_variable,
        function_facts_list, variable_facts_list,
        api_key=api_key, locale=locale,
    ):
        summary_by_module.update(module_batch)
        yield tracker.tick_quiet(len(module_batch))
        try:
            queries = build_module_summary_queries(module_batch, lang=st.lang)
            if queries:
                async for chunk in write_to_graph(client, queries, "PHASE3:MODULE_ANALYSIS"):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:3:MODULE_ANALYSIS] Neo4j 반영 실패 | error=%s", e, exc_info=True)

    yield tracker.end_step(f"클래스 {len(summary_by_module)}개 분석 완료")

    st.summary_by_variable = summary_by_variable
    st.summary_by_module = summary_by_module


# ═══════════════════════════════════════════════════════════════════════
# Step 4: Actor 병합 + Table Description 보강
# ═══════════════════════════════════════════════════════════════════════

async def _run_actor_and_table_enrichment(st, client, tracker, orchestrator) -> AsyncGenerator[bytes, None]:
    """Actor 동의어 통합 + Table Description LLM 보강."""
    api_key = settings.llm.api_key
    locale = settings.locale
    analysis_by_function = st.analysis_by_function or {}
    summary_by_module = st.summary_by_module or {}
    function_facts_list = st.function_facts_list or []

    # ── Actor 통합 ──
    try:
        from analyzer.strategy.framework.linking.actor_merge import (
            apply_module_unified_actors,
            merge_cross_module_actors,
        )

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

        actor_queries = build_actor_queries(analysis_by_function)
        if actor_queries:
            async for chunk in write_to_graph(client, actor_queries, "PHASE3:ACTORS"):
                yield chunk
            actor_count = len({d.get("actor") for d in analysis_by_function.values() if d.get("actor")})
            logger.info("[PHASE:3:ACTOR_MERGE] Actor 노드 생성 | actors=%d", actor_count)
    except Exception as e:
        logger.error("[PHASE:3:ACTOR_MERGE] Actor 처리 실패 | error=%s", e, exc_info=True)

    # ── Table Description 보강 ──
    yield tracker.begin_step(3, 1)
    enriched_table_count = 0
    if api_key and analysis_by_function:
        try:
            enrichment_query = build_table_description_enrichment_query()
            table_results = await client.execute_queries([enrichment_query])
            tables_with_summaries = table_results[0] if table_results and table_results[0] else []

            if tables_with_summaries:
                yield emit_message(f"  ├─ {len(tables_with_summaries)}개 테이블 LLM 설명 보강 중...")
                from util.prompt_registry import prompts

                insights_map = st.table_insights_map or {}

                def _enrich_one_table(table_info: dict) -> Optional[Dict[str, Any]]:
                    name = table_info.get("name", "")
                    schema = table_info.get("schema", "")
                    current_desc = table_info.get("current_description") or ""
                    method_summaries = table_info.get("method_summaries") or []
                    columns_meta = table_info.get("columns") or []
                    table_display = f"{schema}.{name}" if schema else name
                    sentences = [f"[메서드 요약] {s}" for s in method_summaries[:20]]
                    if current_desc:
                        sentences.insert(0, f"[기존 설명] {current_desc}")
                    # Phase 3 Step 2 에서 누적된 tableInsights 의 description 도 신호로 추가
                    insight_slot = insights_map.get(name.lower()) or insights_map.get(name) or {}
                    for desc in (insight_slot.get("descriptions") or [])[:10]:
                        if desc:
                            sentences.append(f"[함수별 테이블 역할] {desc}")
                    col_metadata = {}
                    col_sentences: dict[str, list[str]] = {}
                    for col in columns_meta:
                        col_name = col.get("name", "")
                        if col_name:
                            col_metadata[col_name] = {
                                "dtype": col.get("dtype", ""),
                                "nullable": col.get("nullable"),
                                "is_primary_key": col.get("is_primary_key"),
                                "description": col.get("description", ""),
                            }
                    # tableInsights 로 모인 컬럼 설명을 column_sentences 로 전달
                    for col_name, col_slot in (insight_slot.get("columns") or {}).items():
                        descs = [d for d in (col_slot.get("descriptions") or []) if d]
                        if descs:
                            col_sentences[col_name] = descs[:10]
                    result = prompts.table_summary(
                        table_name=table_display,
                        table_sentences=sentences,
                        column_sentences=col_sentences,
                        column_metadata=col_metadata,
                        locale=locale,
                        api_key=api_key,
                    )
                    if isinstance(result, dict) and result.get("tableDescription"):
                        return {
                            "db": table_info.get("db", ""),
                            "schema": schema,
                            "name": name,
                            "enriched_description": result["tableDescription"],
                            "columns": result.get("columns") or [],
                        }
                    return None

                workers = max(1, settings.concurrency.max_concurrency)
                enrichments: List[Dict[str, Any]] = []
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {executor.submit(_enrich_one_table, t): t for t in tables_with_summaries}
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                            if result:
                                enrichments.append(result)
                        except Exception as e:
                            t = futures[future]
                            logger.error("[PHASE:3:TABLE_ENRICH] 보강 실패 | table=%s error=%s", t.get("name", ""), e)

                if enrichments:
                    enriched_table_count = len(enrichments)
                    try:
                        queries = build_table_description_update_queries(enrichments)
                        for item in enrichments:
                            columns = item.get("columns") or []
                            if columns:
                                queries.extend(
                                    _build_column_description_update_queries(
                                        item.get("db", ""),
                                        item.get("schema", ""),
                                        item.get("name", ""),
                                        columns,
                                    )
                                )
                        if queries:
                            async for chunk in write_to_graph(client, queries, "PHASE3:TABLE_ENRICHMENT"):
                                yield chunk
                    except Exception as e:
                        logger.error("[PHASE:3:TABLE_ENRICH] Neo4j 반영 실패 | error=%s", e, exc_info=True)
        except Exception as e:
            logger.error("[PHASE:3:TABLE_ENRICH] Table 보강 스킵 | error=%s", e, exc_info=True)

    yield tracker.end_step(f"테이블 {enriched_table_count}개 설명 보강")


def _build_column_description_update_queries(
    db: str, schema: str, table_name: str, columns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """LLM table_summary가 반환한 컬럼별 description을 Neo4j Column 노드에 반영."""
    queries = []
    for col in columns:
        col_name = col.get("name", "").strip()
        col_desc = col.get("description", "").strip()
        if not col_name or not col_desc:
            continue
        queries.append({
            "query": (
                "MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)\n"
                "WHERE (t.name = $table_name OR t.fqn ENDS WITH $table_name)\n"
                "  AND toUpper(c.name) = toUpper($col_name)\n"
                "SET c.description = CASE\n"
                "      WHEN c.description IS NULL OR trim(toString(c.description)) = ''\n"
                "      THEN $col_desc\n"
                "      ELSE c.description + ' | ' + $col_desc\n"
                "    END,\n"
                "    c.updated_at = timestamp()\n"
                "RETURN c"
            ),
            "params": {"table_name": table_name, "col_name": col_name, "col_desc": col_desc},
        })
    return queries
