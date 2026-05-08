"""DBMS Phase 3: 후처리 — 프로시저 요약 + 테이블 description 보강.

순서 제어 + emit + LLM 호출. 결정론 매칭/오케스트레이션은 analyzer/common/pipeline,
프롬프트/Cypher 빌더는 dbms 측 모듈 사용.

호출 모듈:
  - `dbms/analysis/procedure_summary.py`         — 프로시저 LLM + Bottom-up Rule
  - `dbms/common/cypher/graph_queries.py`        — Cypher 빌더
  - `analyzer.common.pipeline.table_description_enrichment` — 테이블 enrichment 오케스트레이션
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.common.pipeline.analysis_context import fetch_constants_context
from analyzer.common.pipeline.table_description_enrichment import run_table_enrichment
from analyzer.strategy.dbms.analysis.procedure_summary import assemble_final_analysis
from analyzer.strategy.dbms.analysis.procedure_summary import (
    collect_called_procedure_summaries,
    collect_referenced_symbols_for_unit,
    summarize_procedure,
)
from analyzer.strategy.dbms.common.cypher.graph_queries import (
    build_column_description_queries,
    build_procedure_summary_queries,
    build_table_description_queries,
)
from analyzer.strategy.dbms.common.domain.analysis_constants import VARIABLE_DECLARATION_TYPES
from analyzer.strategy.dbms.common.models.node_models import FileAnalysisContext, FileStatus
from analyzer.common.pipeline.stream_merge import merge_streams
from config.settings import settings
from util.llm_rule_runner import prompts
from util.logger import log_process
from util.stream_emitter import emit_user_message, emit_phase_event

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# 1. 프로시저 요약 — 파일별 PH2_OK 프로시저들에 대해 병렬 LLM 호출
# ───────────────────────────────────────────────────────────────────────────


def _collect_representative_actor(proc_root: Any) -> str:
    """proc_root 하위 모든 노드의 actor 빈도 집계 → 대표 actor 1개 반환.

    LLM 이 자식 노드별로 actor 응답을 줘도, 그것은 결국 unit-root 의 호출자/실행자.
    노이즈 회피 위해 자식 statement 별 ROLE 부착은 제거 (D4 fix), 가장 빈번한
    actor 를 unit-root 에 한 번 부착. 모든 자식이 actor 비어있으면 빈 문자열 반환
    → build_procedure_summary_queries 가 ROLE Cypher 자체를 생략.
    """
    if proc_root is None:
        return ""
    counter: Counter = Counter()

    def walk(node: Any) -> None:
        actor = getattr(node, "actor", "") or ""
        if actor:
            counter[actor] += 1
        for child in getattr(node, "children", None) or []:
            walk(child)

    walk(proc_root)
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def _collect_summary_targets(
    contexts: List[FileAnalysisContext],
) -> List[tuple]:
    """PH2_OK 인 파일의 처리기에서 (ctx, processor, proc_key, info, summaries, proc_root) 수집."""
    targets: List[tuple] = []
    for ctx in contexts:
        if ctx.status != FileStatus.PH2_OK or not ctx.processor:
            continue
        store = getattr(ctx, "unit_summary_store", None) or {}
        if not store:
            continue
        processor = ctx.processor
        for proc_key, info in processor._unit_info.items():
            summaries = store.get(proc_key) or {}
            if not summaries:
                continue
            proc_root = next(
                (n for n in (processor._nodes or [])
                 if n.unit_key == proc_key and n.node_type == info.procedure_type),
                None,
            )
            if proc_root and proc_root.ok:
                targets.append((ctx, processor, proc_key, info, summaries, proc_root))
    return targets


async def _process_one_procedure(
    *,
    ctx: FileAnalysisContext,
    processor: Any,
    proc_key: str,
    info: Any,
    summaries: dict,
    proc_root: Any,
    constants_context: str,
    api_key: str,
    locale: str,
    client: Any,
    results_queue: asyncio.Queue,
    semaphore: asyncio.Semaphore,
) -> None:
    """프로시저 1개의 summary 생성 + EM Cypher 반영."""
    async with semaphore:
        try:
            referenced_symbols = collect_referenced_symbols_for_unit(
                processor._nodes or [], proc_key, VARIABLE_DECLARATION_TYPES,
                constants_context=constants_context,
            )
            called_summary = await collect_called_procedure_summaries(
                client, info.procedure_name, processor.full_directory, processor.file_name,
            )
            deep_compact = proc_root.get_deep_compact_code() if proc_root else ""

            final_summary = await summarize_procedure(
                summaries=summaries,
                deep_compact_code=deep_compact,
                api_key=api_key, locale=locale,
                referenced_symbols=referenced_symbols,
                called_procedure_summaries=called_summary,
            )
            if not final_summary:
                await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
                return

            final_analysis = assemble_final_analysis(proc_root)
            representative_actor = _collect_representative_actor(proc_root)
            queries = build_procedure_summary_queries(
                procedure_type=info.procedure_type,
                procedure_name=info.procedure_name,
                schema_name=info.schema_name or "",
                directory=processor.full_directory,
                file_name=processor.file_name,
                summary=final_summary,
                analysis=final_analysis,
                actor=representative_actor,
            )
            if queries:
                async for chunk in write_to_graph(
                    client, queries, f"DBMS_PHASE2:UNIT_SUMMARY:{info.procedure_name}",
                ):
                    await results_queue.put({"type": "graph_chunk", "chunk": chunk})
            await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
        except Exception as e:
            log_process(
                "PHASE:2", "ERROR",
                f"프로시저 Summary 오류 ({ctx.file_name}/{info.procedure_name}): {e}",
                logging.ERROR, e,
            )
            await results_queue.put({
                "type": "error",
                "procedure": info.procedure_name,
                "file": ctx.file_name,
                "message": str(e),
            })
            raise


async def _run_unit_analysis(
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """프로시저별 LLM summary + EM Cypher 반영을 병렬 실행."""
    targets = _collect_summary_targets(contexts)
    if not targets:
        yield emit_user_message("   ℹ️ 프로시저 Summary: 처리할 프로시저 없음")
        return

    total = len(targets)
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
    constants_context = await fetch_constants_context(ctx.client)

    tasks = [
        asyncio.create_task(_process_one_procedure(
            ctx=fctx, processor=proc, proc_key=pk, info=inf,
            summaries=summ, proc_root=root,
            constants_context=constants_context,
            api_key=ctx.orchestrator.api_key, locale=ctx.orchestrator.locale,
            client=ctx.client, results_queue=results_queue, semaphore=semaphore,
        ))
        for (fctx, proc, pk, inf, summ, root) in targets
    ]

    yield emit_phase_event(2, "AI 분석", "in_progress", 60, {"step": "프로시저 Summary", "total": total})
    completed = 0
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        if result.get("type") == "graph_chunk":
            yield result["chunk"]
            continue
        completed += 1
        if result.get("type") == "error":
            yield emit_user_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['procedure']}: {result.get('message', '')[:50]}")
        else:
            yield emit_user_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['procedure']}: summary 생성")
        yield emit_phase_event(
            2, "AI 분석", "in_progress", 60 + int(completed / total * 15),
            {"step": "프로시저 Summary", "done": completed, "total": total},
        )
    await asyncio.gather(*tasks, return_exceptions=True)
    yield emit_phase_event(2, "AI 분석", "in_progress", 75, {"step": "프로시저 Summary 완료", "total": total})


# ───────────────────────────────────────────────────────────────────────────
# Phase 3 통합 진입점 — 프로시저 요약 → 테이블 description 보강
# ───────────────────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────────────────
# 2. 테이블 description 보강 — DBMS 데이터 변환 + common.run_table_enrichment 호출
# ───────────────────────────────────────────────────────────────────────────


def _gather_table_stores(
    contexts: List[FileAnalysisContext],
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]], str, str]:
    """모든 PH2_OK 파일의 _table_summary_store + _ddl_table_metadata + dbms + datasource 통합."""
    all_stores: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_ddl: Dict[Tuple[str, str], Dict[str, Any]] = {}
    dbms_name = ""
    datasource_name = ""
    for ctx in contexts:
        if ctx.status != FileStatus.PH2_OK or not ctx.processor:
            continue
        proc = ctx.processor
        if not datasource_name:
            datasource_name = getattr(proc, "datasource_name", "") or ""
        store = getattr(proc, "_table_summary_store", None) or {}
        for key, data in store.items():
            entry = all_stores.setdefault(key, {"summaries": set(), "columns": {}})
            entry["summaries"].update(data.get("summaries") or set())
            for col_name, col_data in (data.get("columns") or {}).items():
                if col_name not in entry["columns"]:
                    entry["columns"][col_name] = col_data.copy()
                else:
                    entry["columns"][col_name]["summaries"].update(col_data.get("summaries") or set())
        ddl_meta = getattr(proc, "_ddl_table_metadata", None) or {}
        if ddl_meta:
            all_ddl.update(ddl_meta)
        if not dbms_name and getattr(proc, "dbms", ""):
            dbms_name = proc.dbms
    return all_stores, all_ddl, dbms_name, datasource_name


def _build_column_metadata(
    columns_map: Dict[str, Dict[str, Any]],
    ddl_columns: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """LLM 캐시 키 안정화 — column 이름 정렬 순서로 dict 빌드 (insertion order 결정론)."""
    column_metadata: Dict[str, Dict[str, Any]] = {}
    for entry in sorted(columns_map.values(), key=lambda e: e.get("name", "")):
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
    for col_name in sorted(ddl_columns.keys()):
        ddl_col = ddl_columns[col_name]
        if col_name not in column_metadata:
            column_metadata[col_name] = {
                "dtype": ddl_col.get("dtype") or "",
                "nullable": ddl_col.get("nullable", True),
                "examples": [],
            }
    return column_metadata


def _build_table_inputs(
    all_stores: Dict[Tuple[str, str], Dict[str, Any]],
    all_ddl: Dict[Tuple[str, str], Dict[str, Any]],
    dbms_name: str,
    datasource_name: str,
    locale: str,
) -> List[Dict[str, Any]]:
    """canonical TableInput 리스트 (run_table_enrichment 입력).

    LLM 캐시 키 안정화 — set/dict iteration 결과를 sorted() 로 정규화.
    이전 단계 (Phase 2 LLM 비동기 배치) 의 완료 순서가 매 run 다른데,
    그 결과가 그대로 LLM input list 에 들어가면 같은 의미인데 순서만 다른 prompt
    가 매 run 생성되어 cache miss. sorted() 로 매번 동일 순서 강제.
    """
    inputs: List[Dict[str, Any]] = []
    for table_key, data in sorted(all_stores.items()):
        schema_key, name_key = table_key
        ddl_key = (schema_key.lower(), name_key.lower())
        ddl_meta = all_ddl.get(ddl_key, {})
        ddl_description = (ddl_meta.get("description") or "").strip()
        ddl_columns = ddl_meta.get("columns") or {}

        summaries = sorted(data.get("summaries") or [])
        columns_map = data.get("columns") or {}
        column_sentences: Dict[str, List[str]] = {
            entry["name"]: sorted(entry["summaries"])
            for entry in sorted(columns_map.values(), key=lambda e: e.get("name", ""))
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
            continue

        table_display = f"{schema_key}.{name_key}" if schema_key else name_key
        column_metadata = _build_column_metadata(columns_map, ddl_columns)

        inputs.append({
            "extra": {
                "schema": schema_key, "name": name_key,
                "dbms": dbms_name, "datasource": datasource_name,
            },
            "payload": {
                "table_name": table_display,
                "table_sentences": summaries,
                "column_sentences": column_sentences,
                "column_metadata": column_metadata,
                "locale": locale,
            },
        })
    return inputs


async def _invoke_dbms_table_summary(payload: Dict[str, Any], api_key: str) -> Optional[Dict[str, Any]]:
    """rules/dbms/table_summary 호출. dict 가 아니면 fail-fast."""
    result = await asyncio.to_thread(prompts.execute_raw, "dbms", "table_summary", payload, api_key)
    if result is not None and not isinstance(result, dict):
        logger.error(
            "[TABLE_SUMMARY_LLM] 결과 dict 가 아님 | type=%s payload_table=%s",
            type(result).__name__, payload.get("table_name"),
        )
        raise RuntimeError(f"테이블 요약 결과 오류 ({payload.get('table_name')})")
    return result


def _build_dbms_table_queries(extra: Dict[str, Any], result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """LLM 결과 → :Table / :Column SET 쿼리 리스트 (UNWIND 통합).

    한 테이블 단위 호출 — 테이블 1개 + 컬럼 N개 → 최대 2 UNWIND 쿼리 (table 1, column 1).
    상위 호출자 (`run_table_enrichment`) 가 테이블별 호출 후 결과 누적.
    """
    queries: List[Dict[str, Any]] = []

    table_desc = (result.get("tableDescription") or "").strip()
    if table_desc:
        queries.extend(build_table_description_queries([{
            "schema": extra.get("schema", ""),
            "name":   extra["name"],
            "db":     extra.get("dbms", ""),
            "llm_desc": table_desc,
        }]))

    column_items: List[Dict[str, Any]] = []
    for column_info in result.get("columns") or []:
        column_name = (column_info.get("name") or "").strip()
        llm_column_desc = (column_info.get("description") or "").strip()
        if not column_name or not llm_column_desc:
            continue
        column_items.append({
            "datasource":  extra.get("datasource", ""),
            "schema":      extra.get("schema", ""),
            "table_name":  extra["name"],
            "column_name": column_name,
            "llm_desc":    llm_column_desc,
        })
    queries.extend(build_column_description_queries(column_items))

    return queries


async def _run_dbms_table_enrichment(
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """DBMS 테이블 description 보강: 데이터 변환 + common.run_table_enrichment 호출."""
    all_stores, all_ddl, dbms_name, datasource_name = _gather_table_stores(contexts)
    api_key = ctx.orchestrator.api_key
    locale = ctx.orchestrator.locale

    table_inputs = _build_table_inputs(all_stores, all_ddl, dbms_name, datasource_name, locale)

    async def _invoke(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await _invoke_dbms_table_summary(payload, api_key)

    async for evt in run_table_enrichment(
        client=ctx.client,
        table_inputs=table_inputs,
        invoke_llm=_invoke,
        build_queries=_build_dbms_table_queries,
        log_tag="DBMS_PHASE2:TABLE_SUMMARY",
        phase_num=2, progress_start=85, progress_end=100,
    ):
        yield evt

    # 처리 완료된 store 정리 (메모리 절약)
    for c in contexts:
        proc = c.processor
        if proc is not None and hasattr(proc, "_table_summary_store"):
            proc._table_summary_store.clear()


# ───────────────────────────────────────────────────────────────────────────
# 3. 메인 진입점 — 프로시저 요약 + 테이블 요약 병렬 실행
# ───────────────────────────────────────────────────────────────────────────


async def run_phase_post(
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """Phase 2 후처리 — 프로시저 요약 + 테이블 요약 **병렬** 실행.

    두 단계가 서로 의존성 없으므로 동시 실행으로 LLM API latency 누적 회피.
    Neo4j 쓰기 충돌 (Deadlock 등) 은 client.run_graph_query 의 managed transaction
    이 자동 재시도 → 별도 락 X.
    """
    async for chunk in merge_streams(
        ("unit_summary", _run_unit_analysis(ctx, contexts)),
        ("table_summary", _run_dbms_table_enrichment(ctx, contexts)),
    ):
        yield chunk
