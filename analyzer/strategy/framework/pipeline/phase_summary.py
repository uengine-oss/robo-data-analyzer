"""Phase 3b: Module 요약 + Actor 병합 + Table Description 보강.

2단계:
3. Field + Module 분석
4. Actor 병합 + Table Description 보강
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_emitter import PhaseTracker
from analyzer.strategy.framework.analysis.ast_analyzer import run_module_summary
from analyzer.strategy.framework.common.graph_queries import (
    build_module_summary_queries,
    build_table_description_enrichment_query,
    build_table_description_update_queries,
    build_actor_queries,
)
from analyzer.common.graph_writer import write_to_graph

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Step 3: Field + Module 분석
# ═══════════════════════════════════════════════════════════════════════

async def run_field_and_module_analysis(st, client, tracker) -> AsyncGenerator[bytes, None]:
    """Field 분석 + Module 분석."""
    api_key = settings.llm.api_key
    locale = settings.locale
    variable_facts_list = st.variable_facts_list or []
    module_index = st.module_index or {}
    function_facts_list = st.function_facts_list or []
    analysis_by_function = st.analysis_by_function or {}

    # ── Field 분석 (비활성화) ──
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

async def run_actor_and_table_enrichment(st, client, tracker, orchestrator) -> AsyncGenerator[bytes, None]:
    """Actor 동의어 통합 + Table Description LLM 보강."""
    api_key = settings.llm.api_key
    locale = settings.locale
    analysis_by_function = st.analysis_by_function or {}
    summary_by_module = st.summary_by_module or {}
    function_facts_list = st.function_facts_list or []

    # ── Actor 통합 ──
    try:
        replaced_count = _apply_module_unified_actors(
            analysis_by_function, summary_by_module, function_facts_list,
        )
        if replaced_count:
            logger.info("[PHASE:3:ACTOR_MERGE] 1차 통합 (모듈 내) | replaced=%d", replaced_count)

        if api_key:
            merge_map = await _merge_cross_module_actors(
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
                from util.stream_emitter import emit_message
                yield emit_message(f"  ├─ {len(tables_with_summaries)}개 테이블 LLM 설명 보강 중...")
                from util.llm_prompt_handler import prompts

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


# ═══════════════════════════════════════════════════════════
# Actor 동의어 통합 (← actor_merge.py 인라인)
# ═══════════════════════════════════════════════════════════


def _apply_module_unified_actors(
    analysis_by_function: Dict[str, Dict[str, Any]],
    summary_by_module: Dict[str, Dict[str, Any]],
    function_facts_list: list,
) -> int:
    """1차 통합: module_summary의 unified_actors를 analysis_by_function에 적용."""
    module_to_functions: Dict[str, List[str]] = {}
    for m in function_facts_list or []:
        mk = getattr(m, "module_key", None)
        if mk:
            module_to_functions.setdefault(mk, []).append(m.function_id)

    replaced = 0
    for mk, mdata in summary_by_module.items():
        unified = mdata.get("unified_actors") or {}
        if not unified:
            continue
        syn_to_rep = {}
        for rep, syns in unified.items():
            for syn in syns:
                syn_to_rep[syn] = rep

        fids = module_to_functions.get(mk, [])
        for fid in fids:
            data = analysis_by_function.get(fid)
            if not data:
                continue
            actor = (data.get("actor") or "").strip()
            if actor in syn_to_rep:
                data["actor"] = syn_to_rep[actor]
                replaced += 1

    return replaced


async def _merge_cross_module_actors(
    analysis_by_function: Dict[str, Dict[str, Any]],
    summary_by_module: Dict[str, Dict[str, Any]],
    api_key: str,
    locale: str,
) -> Dict[str, str]:
    """2차: 모듈 간 동의어 통합. LLM 1회 호출."""
    module_actors: Dict[str, Dict[str, Any]] = {}
    for fid, data in analysis_by_function.items():
        actor = (data.get("actor") or "").strip()
        if not actor:
            continue
        module_key = fid.rsplit(".", 1)[0] if "." in fid else fid
        if module_key not in module_actors:
            module_actors[module_key] = {"actors": set(), "summary": ""}
        module_actors[module_key]["actors"].add(actor)

    for mk, mdata in summary_by_module.items():
        if mk in module_actors:
            module_actors[mk]["summary"] = mdata.get("summary", "")

    all_actors: Set[str] = set()
    for ma in module_actors.values():
        all_actors.update(ma["actors"])
    if len(all_actors) <= 1:
        return {}

    lines = []
    for mk, ma in module_actors.items():
        module_name = mk.split(".")[-1] if "." in mk else mk
        module_summary = (ma["summary"] or "")[:150]
        for actor in sorted(ma["actors"]):
            func_summaries = []
            for fid, data in analysis_by_function.items():
                if (data.get("actor") or "").strip() == actor:
                    func_name = fid.rsplit(".", 1)[-1] if "." in fid else fid
                    func_summary = (data.get("summary") or "")[:80]
                    func_summaries.append(f"    - {func_name}: {func_summary}")
            line = f"[{actor}] {module_name}: {module_summary}"
            if func_summaries:
                line += "\n" + "\n".join(func_summaries[:5])
            lines.append(line)
    actor_module_list = "\n\n".join(lines)

    try:
        from util.llm_prompt_handler import prompts
        result = prompts.actor_synonym_merge(actor_module_list, locale, api_key)
        groups = result if isinstance(result, dict) else {}
    except Exception as e:
        logger.error("[PHASE:3:ACTOR_MERGE] LLM 호출 실패 | error=%s", e, exc_info=True)
        return {}

    merge_map: Dict[str, str] = {}
    if isinstance(groups, dict):
        for rep, syns in groups.items():
            if isinstance(syns, list):
                for syn in syns:
                    if syn != rep:
                        merge_map[syn] = rep

    if merge_map:
        logger.info("[ACTOR_MERGE] 모듈 간 동의어 통합: %s", merge_map)
        for fid, data in analysis_by_function.items():
            actor = (data.get("actor") or "").strip()
            if actor in merge_map:
                data["actor"] = merge_map[actor]

    return merge_map
