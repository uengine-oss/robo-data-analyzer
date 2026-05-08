"""Phase 3b: 자식 종합 + Actor 통합 + Table Description 보강.

처리 흐름:
  1. 자식 분석 (PHASE 3-1, 본 모듈 외부) — 메서드 LLM 분석 (필드 분석은 미구현)
  2. 자식 summary 종합 — `run_variable_summary_step` (필드, 현재 no-op) +
                          `run_container_step` (컨테이너 = MODULE + DATATYPE)
  3. 테이블 설명 보강 — 메서드 summary 만 사용 (컨테이너/Actor 무관)
  4. Cross-module actor 통합 — 모든 컨테이너 summary 끝난 뒤 모듈 간 비교

각 step 의존성:
  - run_variable_summary_step    : function 분석 결과의 부수 산출물 (현재 비활성 — function
                                   분석 LLM 출력 스키마에 변수별 의미 미포함)
  - run_container_step           : function 분석 결과 + variable summary (현재 후자 빈 dict)
                                   MODULE + DATATYPE 둘 다 처리 — 둘 다 자식 (메서드/필드)
                                   가지는 컨테이너. ast_collector 가 module_index 에 둘 다
                                   등록하므로 같은 함수가 자연 처리.
  - run_table_description_step   : 메서드 summary 만 의존 (컨테이너/Actor 무관 — 완전 별도)
  - run_cross_module_actor_step  : 모든 컨테이너 summary 끝나야 (모듈 간 비교 + Actor 노드)

호출자 (`framework_pipeline.py`) 에서:

  [병렬] run_container_step + run_table_description_step  (의존성 0)
                          ↓ (모든 컨테이너 끝나야)
   run_cross_module_actor_step

본 모듈은 step 함수만 노출. tracker step emit 도 호출자에서.
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
from util.stream_emitter import PhaseTracker, emit_user_message

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Step: 변수/필드/상수 summary 종합
# ═══════════════════════════════════════════════════════════════════════════


async def run_variable_summary_step(
    ctx: Any,  # FrameworkPhaseContext
    tracker: PhaseTracker,
) -> AsyncGenerator[bytes, None]:
    """변수/필드/상수 summary 종합 — function 분석 결과의 부수 산출물에서 derive.

    설계 (table_description 과 동일 패턴):
      1. function 분석 (PHASE 3-1) 시 LLM 출력에 사용된 변수별 의미 부수 산출
      2. 그 결과를 variable_id 별로 누적 (한 변수 = 여러 function 사용처)
      3. 각 variable_id 별 누적 의미들 → LLM 종합 → variable.summary
      4. 부모 컨테이너 별로 그룹핑되어 있어 다음 단계 (run_container_step) 가
         자연 활용

    현재 비활성 — function 분석 LLM 출력 스키마에 변수별 의미 부수 산출 미구현.
    활성화 시 본 함수 안에 누적 + 종합 + Neo4j 적재 로직 채움.
    state 에 `summary_by_variable` 빈 dict 저장해 다음 step 이 input 으로 받음.
    """
    st = ctx.state

    yield tracker.begin_step(1, 1)

    # 활성화 시 구현 위치:
    #   1. st.analysis_by_function 의 부수 산출물 (referenced_variable_meanings) 누적
    #      → variable_id 별 의미 리스트
    #   2. variable_id 별로 LLM 종합 호출 (table_summary 패턴) → variable.summary
    #   3. build_variable_summary_queries 로 Neo4j 적재
    summary_by_variable: dict = {}
    st.summary_by_variable = summary_by_variable

    msg, evt = tracker.tick(1)
    yield msg
    yield evt
    yield tracker.end_step("필드 summary 비활성 (function 분석 출력 스키마 미확장)")


# ═══════════════════════════════════════════════════════════════════════════
# Step: 컨테이너 (MODULE + DATATYPE) summary 종합 + 1차 actor 적용
# ═══════════════════════════════════════════════════════════════════════════


async def run_container_step(
    ctx: Any,  # FrameworkPhaseContext
    tracker: PhaseTracker,
) -> AsyncGenerator[bytes, None]:
    """컨테이너 summary LLM 종합 + 1차 actor 적용 (per-container).

    컨테이너 = `MODULE` (CLASS/INTERFACE/ENUM/RECORD/FILE/Go STRUCT 등 메서드 품는 코드 단위)
              + `DATATYPE` (C STRUCT/UNION/ENUM 등 필드만 가지는 데이터 컨테이너).
    ast_collector 가 둘 다 module_index 에 등록하므로 `run_module_summary` 가
    같은 패턴으로 자연 처리 — 자식 메서드 + 필드 모아 LLM 종합.

    실행 흐름:
      - module_index 의 컨테이너마다 LLM 호출 (Map-Reduce 청크 분할 내부 처리)
      - 결과 `module_batch` 가 yield 될 때마다 unified_actors 즉시 적용
        (per-container 1차 actor 흡수 — cross-module merge 전 단계)
      - `summary_by_module` (state) 에 누적 — cross-module actor merge 단계용
    """
    st = ctx.state
    client = ctx.client
    api_key = settings.llm.api_key
    locale = settings.locale
    variable_facts_list = st.variable_facts_list or []
    module_index = st.module_index or {}
    function_facts_list = st.function_facts_list or []
    analysis_by_function = st.analysis_by_function or {}
    summary_by_variable = st.summary_by_variable or {}

    container_total = max(1, len(module_index))
    yield tracker.begin_step(2, container_total)
    summary_by_module: dict = {}
    for container_batch in run_module_summary(
        module_index, analysis_by_function, summary_by_variable,
        function_facts_list, variable_facts_list,
        api_key=api_key, locale=locale,
    ):
        summary_by_module.update(container_batch)

        # 1차 actor 적용 (per-container 즉시) — batch 의 unified_actors 만 처리.
        replaced = apply_module_unified_actors(
            analysis_by_function, container_batch, function_facts_list,
        )
        if replaced:
            logger.info(
                "[PHASE:3:ACTOR] 1차 컨테이너 내 통합 | batch=%d replaced=%d",
                len(container_batch), replaced,
            )

        msg, evt = tracker.tick(len(container_batch))
        yield msg
        yield evt
        queries = build_module_summary_queries(container_batch, lang=st.lang)
        if queries:
            async for chunk in write_to_graph(client, queries, "PHASE3:CONTAINER_ANALYSIS"):
                yield chunk

    yield tracker.end_step(f"컨테이너 {len(summary_by_module)}개 분석 완료")

    st.summary_by_module = summary_by_module


# ═══════════════════════════════════════════════════════════════════════════
# Step: Table description 보강 (method summary 만 의존 — Module/Actor 와 별도 흐름)
# ═══════════════════════════════════════════════════════════════════════════


async def run_table_description_step(
    ctx: Any,  # FrameworkPhaseContext
) -> AsyncGenerator[bytes, None]:
    """Table description LLM 보강 — method summary 만 사용 (Module/Actor 무관).

    Module step 과 병렬 실행되므로 PhaseTracker 사용 X (step_idx race 방지).
    진행 메시지는 자체 emit_user_message 로.
    """
    st = ctx.state
    client = ctx.client
    api_key = settings.llm.api_key
    locale = settings.locale
    analysis_by_function = st.analysis_by_function or {}

    if not (api_key and analysis_by_function):
        return

    yield emit_user_message("  ├─ 테이블 설명 보강 (LLM) 시작")
    async for chunk in _run_framework_table_enrichment(
        client=client,
        insights_map=st.table_insights_map or {},
        api_key=api_key,
        locale=locale,
    ):
        yield chunk
    yield emit_user_message("  └─ 테이블 설명 보강 완료")


# ═══════════════════════════════════════════════════════════════════════════
# Step: Cross-module actor merge + Actor 노드 그래프 작성
# (Module summary 모두 끝난 후 — 모듈 간 비교 필요)
# ═══════════════════════════════════════════════════════════════════════════


async def run_cross_module_actor_step(
    ctx: Any,  # FrameworkPhaseContext
) -> AsyncGenerator[bytes, None]:
    """모듈 간 actor 동의어 통합 (LLM 1회) + Actor 노드 그래프 작성."""
    st = ctx.state
    client = ctx.client
    api_key = settings.llm.api_key
    locale = settings.locale
    analysis_by_function = st.analysis_by_function or {}
    summary_by_module = st.summary_by_module or {}

    if api_key and summary_by_module:
        yield emit_user_message("  ├─ Actor 동의어 통합 (모듈 간) 분석 중...")
        merge_map = await merge_cross_module_actors(
            analysis_by_function, summary_by_module, api_key, locale,
        )
        yield emit_user_message(
            f"  ├─ Actor 동의어 통합 완료 ({len(merge_map)}건 매핑)"
        )
        if merge_map:
            logger.info("[PHASE:3:ACTOR_MERGE] 2차 통합 (모듈 간) | merge_map=%s", merge_map)

    # Actor 노드 생성 + ROLE 관계 반영
    actor_queries = build_actor_queries(analysis_by_function)
    if actor_queries:
        async for chunk in write_to_graph(client, actor_queries, "PHASE3:ACTORS"):
            yield chunk
        actor_count = len({d.get("actor") for d in analysis_by_function.values() if d.get("actor")})
        logger.info("[PHASE:3:ACTOR] Actor 노드 생성 | actors=%d", actor_count)


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

    # LLM 캐시 키 안정화 — Neo4j collect() 가 비결정론 순서라 정렬 후 prefix 부착.
    # 의미 순서 ([기존 설명] → [메서드 요약] → [함수별 테이블 역할]) 는 prefix 카테고리로 유지.
    sentences: List[str] = []
    if current_desc:
        sentences.append(f"[기존 설명] {current_desc}")
    sentences.extend(f"[메서드 요약] {s}" for s in sorted(method_summaries)[:20])

    insight_slot = insights_map.get(name.lower()) or insights_map.get(name) or {}
    for desc in sorted(insight_slot.get("descriptions") or [])[:10]:
        if desc:
            sentences.append(f"[함수별 테이블 역할] {desc}")

    col_metadata: Dict[str, Dict[str, Any]] = {}
    col_sentences: Dict[str, List[str]] = {}
    sorted_cols = sorted(columns_meta, key=lambda c: c.get("name", ""))
    for col in sorted_cols:
        col_name = col.get("name", "")
        if not col_name:
            continue
        col_metadata[col_name] = {
            "dtype": col.get("dtype", ""),
            "nullable": col.get("nullable"),
            "is_primary_key": col.get("is_primary_key"),
            "description": col.get("description", ""),
        }
    for col_name in sorted((insight_slot.get("columns") or {}).keys()):
        col_slot = insight_slot["columns"][col_name]
        descs = sorted({d for d in (col_slot.get("descriptions") or []) if d})
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

    yield emit_user_message(f"  ├─ {len(tables_with_summaries)}개 테이블 LLM 설명 보강 중...")

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
            "analyzed_description": result["tableDescription"],
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
