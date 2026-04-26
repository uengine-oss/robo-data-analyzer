"""DBMS Phase 3: 후처리 — 프로시저 요약 + 테이블 description 보강.

순서 제어 + emit + 다른 모듈 호출만. 실제 LLM 호출/Cypher 빌더는 enrichment/analysis 의
전용 모듈이 담당.

호출 모듈:
  - `dbms/analysis/procedure_summary.py`         — LLM + chunk 분할 + Bottom-up Rule
  - `dbms/common/graph_queries.py`              — PROCEDURE summary + EM Cypher 빌더
  - `dbms/enrichment/table_description_enrichment.py` — 테이블/컬럼 요약 LLM + 벡터라이징
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from analyzer.common.cypher.graph_writer import write_to_graph
from analyzer.common.pipeline.analysis_context import fetch_constants_context
from analyzer.strategy.dbms.analysis.procedure_summary import assemble_final_analysis
from analyzer.strategy.dbms.analysis.procedure_summary import (
    collect_called_procedure_summaries,
    collect_variable_declarations,
    summarize_procedure,
)
from analyzer.strategy.dbms.common.domain.analysis_constants import VARIABLE_DECLARATION_TYPES
from analyzer.strategy.dbms.common.cypher.graph_queries import build_procedure_summary_queries
from analyzer.strategy.dbms.common.models.node_models import FileAnalysisContext, FileStatus
from analyzer.strategy.dbms.enrichment.table_description_enrichment import (
    run_table_description_enrichment,
)
from config.settings import settings
from util.logger import log_process
from util.stream_emitter import emit_message, emit_phase_event

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# 1. 프로시저 요약 — 파일별 PH2_OK 프로시저들에 대해 병렬 LLM 호출
# ───────────────────────────────────────────────────────────────────────────


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
            var_decls = collect_variable_declarations(
                processor._nodes or [], proc_key, VARIABLE_DECLARATION_TYPES,
            )
            called_summary = await collect_called_procedure_summaries(
                client, info.procedure_name, processor.full_directory, processor.file_name,
            )
            deep_compact = proc_root.get_deep_compact_code() if proc_root else ""

            final_summary = await summarize_procedure(
                summaries=summaries,
                deep_compact_code=deep_compact,
                api_key=api_key, locale=locale,
                variable_declarations=var_decls,
                constants_context=constants_context,
                called_procedure_summaries=called_summary,
            )
            if not final_summary:
                await results_queue.put({"type": "success", "procedure": info.procedure_name, "file": ctx.file_name})
                return

            final_analysis = assemble_final_analysis(proc_root)
            queries = build_procedure_summary_queries(
                procedure_type=info.procedure_type,
                procedure_name=info.procedure_name,
                schema_name=info.schema_name or "",
                directory=processor.full_directory,
                file_name=processor.file_name,
                summary=final_summary,
                analysis=final_analysis,
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
        yield emit_message("   ℹ️ 프로시저 Summary: 처리할 프로시저 없음")
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
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['procedure']}: {result.get('message', '')[:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['procedure']}: summary 생성")
        yield emit_phase_event(
            2, "AI 분석", "in_progress", 60 + int(completed / total * 15),
            {"step": "프로시저 Summary", "done": completed, "total": total},
        )
    await asyncio.gather(*tasks, return_exceptions=True)
    yield emit_phase_event(2, "AI 분석", "in_progress", 75, {"step": "프로시저 Summary 완료", "total": total})


# ───────────────────────────────────────────────────────────────────────────
# Phase 3 통합 진입점 — 프로시저 요약 → 테이블 description 보강
# ───────────────────────────────────────────────────────────────────────────


async def run_phase_post(
    ctx: Any,
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """Phase 3 후처리 — 프로시저 요약 → 테이블 요약 (순서 보장)."""
    async for chunk in _run_unit_analysis(ctx, contexts):
        yield chunk
    async for chunk in run_table_description_enrichment(
        client=ctx.client,
        contexts=contexts,
        api_key=ctx.orchestrator.api_key,
        locale=ctx.orchestrator.locale,
    ):
        yield chunk
