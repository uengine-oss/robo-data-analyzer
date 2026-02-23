"""Phase 2: LLM 배치 분석 - DBMS

Phase 1 성공 파일에 대해 LLM으로 노드 요약, 변수 역할, 테이블 관계 분석.
핵심 로직은 ast_processor.run_llm_analysis()에서 수행.
dbms_pipeline에서 호출.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import log_process


async def run_phase_llm(
    pipeline: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2: LLM 배치 분석 - Phase 1 성공 파일 병렬 처리
    
    ctx.processor.run_llm_analysis() 호출 후, 프로시저 요약/User Story/테이블 요약 후처리.
    
    Args:
        pipeline: DbmsPipeline 인스턴스 (총괄 파이프라인)
        contexts: Phase 1 성공 파일 컨텍스트 리스트
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    if not contexts:
        yield emit_message("   ℹ️ 분석 대상 파일 없음")
        return
    
    completed = 0
    total = len(contexts)
    results_queue: asyncio.Queue = asyncio.Queue()
    max_workers = pipeline._file_semaphore._value if hasattr(pipeline._file_semaphore, '_value') else 5
    
    yield emit_message("")
    yield emit_message(f"┌─────────────────────────────────────────────────────────┐")
    yield emit_message(f"│  📊 Phase 2: AI 분석 시작                                │")
    yield emit_message(f"│     총 {total}개 파일, 동시 처리: {max_workers}개                         │")
    yield emit_message(f"└─────────────────────────────────────────────────────────┘")

    async def analyze_file(ctx: FileAnalysisContext):
        async with pipeline._file_semaphore:
            try:
                if not ctx.processor:
                    raise RuntimeError(f"Phase 1에서 프로세서 초기화 실패: {ctx.file_name}")
                all_nodes = {}
                all_relationships = {}
                
                async def on_queries_ready(queries, batch_id, total_batches):
                    if not queries:
                        return
                    async with pipeline._cypher_lock:
                        async for batch_result in client.run_graph_query(queries):
                            for node in batch_result.get("Nodes", []):
                                all_nodes[node["Node ID"]] = node
                            for rel in batch_result.get("Relationships", []):
                                all_relationships[rel["Relationship ID"]] = rel
                            await results_queue.put({
                                "type": "batch_progress",
                                "file": ctx.file_name,
                                "batch_id": batch_id,
                                "total_batches": total_batches,
                                "graph": {
                                    "Nodes": batch_result.get("Nodes", []),
                                    "Relationships": batch_result.get("Relationships", []),
                                },
                            })
                
                analysis_queries, failed_batch_count, failed_details, unit_summary_store = await ctx.processor.run_llm_analysis(
                    on_queries_ready=on_queries_ready,
                )
                ctx.unit_summary_store = unit_summary_store
                graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
                ctx.status = FileStatus.PH2_OK
                await results_queue.put({
                    "type": "success",
                    "file": ctx.file_name,
                    "graph": graph,
                    "query_count": len(analysis_queries),
                    "failed_batches": failed_batch_count,
                    "failed_details": failed_details,
                })
                if failed_batch_count > 0:
                    raise RuntimeError(f"{ctx.file_name}: {failed_batch_count}개 배치 실패")
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2 오류 ({ctx.file_name}): {e}", logging.ERROR, e)
                ctx.status = FileStatus.PH2_FAIL
                ctx.error_message = str(e)[:100]
                await results_queue.put({"type": "error", "file": ctx.file_name, "message": str(e)})
                raise

    tasks = [asyncio.create_task(analyze_file(ctx)) for ctx in contexts]

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
            log_process("STREAM", "BATCH", f"{result.get('file')}: 스트림 전송 완료 (배치 {batch_label}, 노드 {node_count}개)")
            yield emit_message(f"│     📦 배치 {batch_label} 저장 완료 (노드 {node_count}개)")
            if graph:
                yield emit_data(graph=graph, current_file=result.get("file"))
            continue
        
        completed += 1
        remaining = total - completed
        phase2_progress = 50 + int(completed / total * 50)
        
        if result_type == "error":
            yield emit_message(f"├─ ❌ [{completed}/{total}] {result['file']}")
            yield emit_message(f"│     오류: {result['message'][:80]}")
            yield emit_message(f"│     남은 파일: {remaining}개")
            stats.mark_file_failed(result['file'], "Phase2 실패")
            yield emit_phase_event(phase_num=2, phase_name="AI 분석", status="in_progress", progress=phase2_progress, details={"file": result['file'], "status": "failed", "completed": completed, "total": total})
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
            
            yield emit_phase_event(phase_num=2, phase_name="AI 분석", status="in_progress", progress=phase2_progress, details={"file": result['file'], "queries": result['query_count'], "nodes_updated": node_count, "relationships_updated": rel_count, "completed": completed, "total": total})
            yield emit_data(analysis_progress=phase2_progress, current_file=result["file"])

    await asyncio.gather(*tasks, return_exceptions=True)
    
    from analyzer.strategy.dbms.phase_post import run_phase_post
    async for chunk in run_phase_post(pipeline, contexts, client, orchestrator, stats):
        yield chunk
    
    yield emit_phase_event(2, "AI 분석", "completed", 100, {"batches": stats.llm_batches_executed})
