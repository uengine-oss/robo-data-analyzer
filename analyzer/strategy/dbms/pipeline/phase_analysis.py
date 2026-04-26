"""DBMS Phase 2: LLM 배치 분석 — Phase 1 성공 파일 병렬 처리 + 후처리."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from analyzer.common.cypher.graph_writer import run_graph_queries
from analyzer.strategy.dbms.common.models.node_models import FileAnalysisContext, FileStatus
from analyzer.strategy.dbms.pipeline.phase_summary import run_phase_post
from config.settings import settings
from util.logger import log_process
from util.stream_emitter import emit_data, emit_message, emit_phase_event

logger = logging.getLogger(__name__)


async def run_phase_llm(
    ctx: Any,  # DbmsPhaseContext
    contexts: List[FileAnalysisContext],
) -> AsyncGenerator[bytes, None]:
    """Phase 2: LLM 배치 분석."""
    client = ctx.client
    stats = ctx.stats
    if not contexts:
        yield emit_message("   ℹ️ 분석 대상 파일 없음")
        return

    completed = 0
    total = len(contexts)
    results_queue: asyncio.Queue = asyncio.Queue()
    max_workers = settings.concurrency.file_concurrency

    yield emit_message("")
    yield emit_message(f"┌─────────────────────────────────────────────────────────┐")
    yield emit_message(f"│  📊 Phase 2: AI 분석 시작                                │")
    yield emit_message(f"│     총 {total}개 파일, 동시 처리: {max_workers}개                         │")
    yield emit_message(f"└─────────────────────────────────────────────────────────┘")

    async def analyze_file(file_ctx: FileAnalysisContext):
        async with ctx.file_semaphore:
            try:
                if not file_ctx.processor:
                    raise RuntimeError(f"Phase 1에서 프로세서 초기화 실패: {file_ctx.file_name}")
                all_nodes = {}
                all_relationships = {}

                async def on_queries_ready(queries, batch_id, total_batches):
                    if not queries:
                        return
                    async for batch in run_graph_queries(client, queries):
                        for node in batch.get("Nodes", []):
                            all_nodes[node["Node ID"]] = node
                        for rel in batch.get("Relationships", []):
                            all_relationships[rel["Relationship ID"]] = rel
                        await results_queue.put({
                            "type": "batch_progress",
                            "file": file_ctx.file_name,
                            "batch_id": batch_id,
                            "total_batches": total_batches,
                            "graph": batch,
                        })

                analysis_queries, failed_batch_count, failed_details, unit_summary_store = await file_ctx.processor.run_llm_analysis(
                    on_queries_ready=on_queries_ready,
                )
                file_ctx.unit_summary_store = unit_summary_store
                graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
                file_ctx.status = FileStatus.PH2_OK
                await results_queue.put({
                    "type": "success",
                    "file": file_ctx.file_name,
                    "graph": graph,
                    "query_count": len(analysis_queries),
                    "failed_batches": failed_batch_count,
                    "failed_details": failed_details,
                })
                if failed_batch_count > 0:
                    raise RuntimeError(f"{file_ctx.file_name}: {failed_batch_count}개 배치 실패")
            except Exception as e:
                log_process("PHASE:2", "ERROR", f"Phase 2 오류 ({file_ctx.file_name}): {e}", logging.ERROR, e)
                file_ctx.status = FileStatus.PH2_FAIL
                file_ctx.error_message = str(e)[:100]
                await results_queue.put({"type": "error", "file": file_ctx.file_name, "message": str(e)})
                raise

    tasks = [asyncio.create_task(analyze_file(file_ctx)) for file_ctx in contexts]

    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=600.0)
        result_type = result.get("type", "")

        if result_type == "warning":
            yield emit_message(f"   ⚠️ {result['file']}: {result['message']}")
            continue

        if result_type == "batch_progress":
            batch_id = result.get("batch_id", 0)
            total_batches = result.get("total_batches", 0)
            graph = result.get("graph")
            node_count = len(graph.get("Nodes", [])) if graph else 0
            batch_label = f"#{batch_id}/{total_batches}" if total_batches > 0 else f"#{batch_id}"
            log_process("PHASE:2", "STREAM", f"{result.get('file')}: 스트림 전송 완료 (배치 {batch_label}, 노드 {node_count}개)")
            yield emit_message(f"│     📦 배치 {batch_label} 저장 완료 (노드 {node_count}개)")
            if graph:
                yield emit_data(graph=graph, current_file=result.get("file"))
            continue

        completed += 1
        remaining = total - completed
        phase2_progress = int(completed / total * 100)

        if result_type == "error":
            yield emit_message(f"├─ ❌ [{completed}/{total}] {result['file']}")
            yield emit_message(f"│     오류: {result['message'][:80]}")
            yield emit_message(f"│     남은 파일: {remaining}개")
            stats.mark_file_failed(result['file'], "Phase2 실패")
            yield emit_phase_event(2, "AI 분석", "in_progress", phase2_progress,
                                   {"file": result['file'], "status": "failed", "completed": completed, "total": total})
        else:
            stats.llm_batches_executed += 1
            graph = result["graph"]
            stats.add_graph_result(graph, is_static=False)
            failed_batches = result.get("failed_batches", 0)
            failed_details = result.get("failed_details", [])
            node_count = len(graph.get("Nodes", []))
            rel_count = len(graph.get("Relationships", []))
            status_icon = "✅" if failed_batches == 0 else "⚠️"

            yield emit_message(f"├─ {status_icon} [{completed}/{total}] {result['file']}")
            yield emit_message(f"│     쿼리: {result['query_count']}개, 노드: {node_count}개, 관계: {rel_count}개")
            if remaining > 0:
                yield emit_message(f"│     남은 파일: {remaining}개")

            summary_added = sum(1 for n in graph.get("Nodes", []) if n.get("Properties", {}).get("summary"))
            table_desc_added = sum(1 for n in graph.get("Nodes", []) if "Table" in (n.get("Labels") or []) and n.get("Properties", {}).get("analyzed_description"))
            if summary_added or table_desc_added:
                detail_parts = []
                if summary_added:
                    detail_parts.append(f"요약 {summary_added}개")
                if table_desc_added:
                    detail_parts.append(f"테이블 설명 {table_desc_added}개")
                yield emit_message(f"│     생성: {', '.join(detail_parts)}")

            if failed_details:
                stats.llm_batches_failed += len(failed_details)
                for detail in failed_details[:3]:
                    yield emit_message(f"│     ⚠️ 배치 #{detail['batch_id']}: {detail['error'][:50]}")

            yield emit_phase_event(2, "AI 분석", "in_progress", phase2_progress,
                                   {"file": result['file'], "queries": result['query_count'],
                                    "nodes_updated": node_count, "relationships_updated": rel_count,
                                    "completed": completed, "total": total})
            yield emit_data(analysis_progress=phase2_progress, current_file=result["file"])

    await asyncio.gather(*tasks, return_exceptions=True)

    # 후처리 (프로시저 요약, 테이블 요약)
    async for chunk in run_phase_post(ctx, contexts):
        yield chunk

    # `completed` 는 호출자(dbms_pipeline) 가 wrapping emit (Framework 패턴 통일).
