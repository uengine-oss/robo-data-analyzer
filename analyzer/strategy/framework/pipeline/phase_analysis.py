"""Phase 3a: 컨텍스트 수집 + 코드 분석 LLM 배치.

2단계:
1. 컨텍스트 수집 (상수, 참조변수, 테이블 샘플)
2. 메서드 분석 (LLM 배치: summary + stereotype + dependency + calls + variables + tableInsights)
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List

from client.neo4j_client import Neo4jClient
from config.settings import settings
from util.stream_emitter import PhaseTracker
from analyzer.strategy.framework.analysis.ast_analyzer import analyze_and_enrich
from analyzer.strategy.framework.common.graph_queries import (
    build_analysis_result_queries,
    build_uml_dependency_queries,
)
from analyzer.common.graph_writer import write_to_graph

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Step 1: 컨텍스트 수집
# ═══════════════════════════════════════════════════════════════════════

async def collect_analysis_context(st, client, orchestrator) -> dict:
    """상수, 참조변수, 테이블 샘플, 코드베이스 컨벤션(codebase profile), 함수별 R/W 맵 수집."""
    ctx: dict = {
        "constants_context": "",
        "referenced_vars_by_function": {},
        "table_sample_data": "",
        "codebase_conventions": "",
        "reads_writes_by_function": {},
    }

    # 함수별 READS/WRITES 테이블 맵 (function_table_edges 기반)
    # 기존 reads_writes_summary 가 SqlStatement 경로만 보던 한계 보완.
    rw_by_fn: dict[str, list[tuple[str, str]]] = {}
    for e in (st.function_table_edges or []):
        fid = getattr(e, "function_id", "") or ""
        tbl = (getattr(e, "table_name", "") or "").strip()
        rel = (getattr(e, "relation", "") or "").strip().upper()
        if not (fid and tbl and rel):
            continue
        pair = (tbl, rel)
        bucket = rw_by_fn.setdefault(fid, [])
        if pair not in bucket:
            bucket.append(pair)
    ctx["reads_writes_by_function"] = rw_by_fn
    if rw_by_fn:
        logger.info(
            "[PHASE:3:CONTEXT] 함수별 R/W 맵 수집 | functions=%d edges=%d",
            len(rw_by_fn), sum(len(v) for v in rw_by_fn.values()),
        )

    # 코드베이스 컨벤션 (Phase 1 codebase_profile.summary_sections 3블록)
    cp = getattr(st, "codebase_profile", None)
    if cp and getattr(cp, "summary_sections", None):
        sections = cp.summary_sections
        blocks: list[str] = []
        if sections.get("framework"):
            blocks.append(sections["framework"])
        if sections.get("data_access"):
            blocks.append(sections["data_access"])
        if sections.get("idioms"):
            blocks.append(sections["idioms"])
        if blocks:
            ctx["codebase_conventions"] = "\n\n".join(blocks)
            logger.info(
                "[PHASE:3:CONTEXT] 코드베이스 컨벤션 주입 | sections=%s len=%d",
                ",".join(sorted(sections.keys())), len(ctx["codebase_conventions"]),
            )

    # 상수 컨텍스트
    from analyzer.common.analysis_context import fetch_constants_context
    ctx["constants_context"] = await fetch_constants_context(client)

    # 참조 변수 + INIT_BY 초기화 출처 컨텍스트
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

    # 테이블 샘플 데이터 — 함수별 caller 기준 필터링 + 호출 체인 전이적 R/W 포함
    reads_writes = st.reads_writes or []
    try:
        from analyzer.common.analysis_context import fetch_table_context
        # 프로젝트 전체 테이블 집합 (fallback 용)
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
            # 각 테이블별 섹션을 한 번만 생성하여 캐시
            table_to_section: dict[str, str] = {}
            for tname in table_names:
                section = await fetch_table_context(client, [tname], datasource_name)
                if section:
                    table_to_section[tname] = section
            # 전체 fallback (직접·간접 R/W 없는 함수용)
            ctx["table_sample_data"] = "\n\n".join(table_to_section.values())

            # 함수별 직접 R/W 테이블 집합
            direct: dict[str, set[str]] = {
                fid: {t for t, _op in pairs} for fid, pairs in rw_by_fn.items()
            }
            # 호출 그래프로 전이적 확장 (상위 함수가 하위 호출 경유 접근하는 테이블 포함)
            callees: dict[str, set[str]] = {}
            for ce in (st.calls_edges or []):
                c = getattr(ce, "caller_function_id", "") or ""
                cal = getattr(ce, "callee_function_id", "") or ""
                if c and cal:
                    callees.setdefault(c, set()).add(cal)

            def transitive(fid: str, seen: set[str]) -> set[str]:
                if fid in seen:
                    return set()
                seen.add(fid)
                acc = set(direct.get(fid, set()))
                for cal in callees.get(fid, ()):
                    acc |= transitive(cal, seen)
                return acc

            all_fids = {m.function_id for m in (st.function_facts_list or [])}
            ts_by_fn: dict[str, str] = {}
            for fid in all_fids:
                trans_tables = transitive(fid, set())
                if not trans_tables:
                    continue
                sections = [table_to_section[t] for t in sorted(trans_tables) if t in table_to_section]
                if sections:
                    ts_by_fn[fid] = "\n\n".join(sections)
            ctx["table_sample_data_by_function"] = ts_by_fn

            logger.info(
                "[PHASE:3:CONTEXT] 테이블 샘플 수집 | 전체 tables=%d 전체 data_len=%d 함수별 맞춤=%d",
                len(table_names), len(ctx["table_sample_data"]), len(ts_by_fn),
            )
    except Exception as e:
        logger.error("[PHASE:3:CONTEXT] 테이블 샘플 수집 실패 | error=%s", e, exc_info=True)

    return ctx


# ═══════════════════════════════════════════════════════════════════════
# Step 2: 메서드 분석 (코드 분석 LLM 배치)
# ═══════════════════════════════════════════════════════════════════════

async def run_code_analysis(
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
        table_sample_data_by_function=summary_ctx.get("table_sample_data_by_function") or None,
        constants_context=summary_ctx.get("constants_context") or None,
        referenced_vars_by_function=summary_ctx.get("referenced_vars_by_function") or None,
        codebase_conventions=summary_ctx.get("codebase_conventions") or None,
        reads_writes_by_function=summary_ctx.get("reads_writes_by_function") or None,
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
