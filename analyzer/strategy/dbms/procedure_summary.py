"""프로시저 Summary 생성 - DBMS

llm_phase.py에서 분리된 Phase 2.1 로직입니다.
모든 파일의 모든 프로시저를 병렬로 처리하여 프로시저 summary를 생성하고 즉시 저장합니다.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, List, Dict

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from config.settings import settings
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import calculate_code_token, log_process


def _split_summaries_by_token(summaries: dict, max_token: int) -> List[dict]:
    """토큰 기준으로 summaries를 청크로 분할합니다."""
    if not summaries:
        return []
    
    chunks = []
    current_chunk = {}
    current_tokens = 0
    
    for key, value in summaries.items():
        item_text = f"{key}: {value}"
        item_tokens = calculate_code_token(item_text)
        
        if current_tokens + item_tokens > max_token and current_chunk:
            chunks.append(current_chunk)
            current_chunk = {}
            current_tokens = 0
        
        current_chunk[key] = value
        current_tokens += item_tokens
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


async def run_phase2_1_procedure_summary(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.1: 프로시저 Summary 생성 (병렬 처리)
    
    모든 파일의 모든 프로시저를 병렬로 처리하여 프로시저 summary를 생성하고 즉시 저장합니다.
    """
    from analyzer.strategy.dbms.ast_processor import analyze_summary_only
    
    MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
    
    success_contexts = [ctx for ctx in contexts 
                       if ctx.status == FileStatus.PH2_OK 
                       and ctx.processor 
                       and hasattr(ctx, 'unit_summary_store') 
                       and ctx.unit_summary_store]
    
    if not success_contexts:
        yield emit_message("   ℹ️ Phase 2.1: 처리할 파일 없음")
        return
    
    # 모든 프로시저 수집
    all_procedures = []
    for ctx in success_contexts:
        processor = ctx.processor
        procedures = processor._unit_info
        for proc_key, info in procedures.items():
            summaries = ctx.unit_summary_store.get(proc_key, {})
            if summaries:
                proc_root = next(
                    (n for n in (processor._nodes or []) 
                     if n.unit_key == proc_key and n.parent is None),
                    None,
                )
                if proc_root and proc_root.ok:
                    all_procedures.append((ctx, processor, proc_key, info, summaries, proc_root))
    
    if not all_procedures:
        yield emit_message("   ℹ️ Phase 2.1: 처리할 프로시저 없음")
        return
    
    total = len(all_procedures)
    completed = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
    
    async def process_procedure(ctx: FileAnalysisContext, processor, proc_key: str, info, summaries: dict, proc_root):
        """단일 프로시저의 summary 생성 및 저장"""
        async with semaphore:
            try:
                # 청크 분할
                chunks = _split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
                if not chunks:
                    final_summary = ""
                else:
                    log_process("ANALYZE", "PHASE_2.1", f"📦 {info.procedure_name}: summary 청크 분할 완료 ({len(chunks)}개 청크)")
                    
                    # 동시 처리 수 제한
                    max_concurrent = settings.concurrency.max_concurrency
                    chunk_semaphore = asyncio.Semaphore(max_concurrent)
                    
                    async def process_chunk(chunk: dict) -> str:
                        result = await asyncio.to_thread(
                            analyze_summary_only, chunk, orchestrator.api_key, orchestrator.locale, ""
                        )
                        # 결과 검증 (간단한 검증만 수행)
                        if not isinstance(result, dict):
                            return ""
                        return result.get('summary', '')
                    
                    # Semaphore로 동시 처리 수 제한
                    async def process_chunk_with_limit(chunk: dict) -> str:
                        async with chunk_semaphore:
                            return await process_chunk(chunk)
                    
                    chunk_results = await asyncio.gather(*[process_chunk_with_limit(chunk) for chunk in chunks])
                    chunk_results = [r for r in chunk_results if r]
                    
                    if not chunk_results:
                        raise RuntimeError(f"{info.procedure_name}: 청크 처리 결과가 모두 비어있음")
                    
                    # 통합
                    if len(chunk_results) == 1:
                        final_summary = chunk_results[0]
                    else:
                        combined = {f"CHUNK_{i+1}": s for i, s in enumerate(chunk_results)}
                        result = await asyncio.to_thread(
                            analyze_summary_only, combined, orchestrator.api_key, orchestrator.locale, ""
                        )
                        # 결과 검증
                        if not isinstance(result, dict):
                            final_summary = "\n\n".join(chunk_results)
                        else:
                            final_summary = result.get('summary') or "\n\n".join(chunk_results)
                    
                    log_process("ANALYZE", "PHASE_2.1", f"✅ {info.procedure_name}: 프로시저 summary 생성 완료")
                
                if not final_summary:
                    await results_queue.put({
                        "type": "success",
                        "procedure": info.procedure_name,
                        "file": ctx.file_name,
                        "queries": [],
                    })
                    return
                
                # 즉시 저장 (프로시저 summary만)
                queries = [{
                    "query": (
                        f"MATCH (__cy_n__:{info.procedure_type} {{procedure_name: $procedure_name, directory: $directory, file_name: $file_name}})\n"
                        f"SET __cy_n__.summary = $summary\n"
                        f"RETURN __cy_n__"
                    ),
                    "params": {
                        "procedure_name": info.procedure_name,
                        "directory": processor.full_directory,
                        "file_name": processor.file_name,
                        "summary": str(final_summary),
                    }
                }]
                
                # 저장 및 스트리밍
                if queries:
                    async with analyzer._cypher_lock:
                        async for batch_result in client.run_graph_query(queries):
                            yield emit_data(
                                graph={
                                    "Nodes": batch_result.get("Nodes", []),
                                    "Relationships": batch_result.get("Relationships", []),
                                }
                            )
                
                # final_summary를 ctx에 저장 (Phase 2.2에서 사용)
                if not hasattr(ctx, 'procedure_summaries'):
                    ctx.procedure_summaries = {}
                ctx.procedure_summaries[proc_key] = {
                    "final_summary": final_summary,
                    "procedure_info": info,
                }
                
                await results_queue.put({
                    "type": "success",
                    "procedure": info.procedure_name,
                    "file": ctx.file_name,
                    "queries": queries,
                })
                
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2.1 오류 ({ctx.file_name}/{info.procedure_name}): {e}", logging.ERROR, e)
                await results_queue.put({
                    "type": "error",
                    "procedure": info.procedure_name,
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise
    
    # Phase 2.1 시작 이벤트
    yield emit_phase_event(2.1, "프로시저 Summary 생성", "started", 0, {"canPause": True, "procedures": total})
    
    # 모든 프로시저 병렬 처리
    tasks = [asyncio.create_task(process_procedure(ctx, proc, pk, inf, summ, pr)) 
             for ctx, proc, pk, inf, summ, pr in all_procedures]
    
    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['procedure']}: {result['message'][:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['procedure']}: summary 생성")
            
            progress = int(completed / total * 100)
            yield emit_phase_event(2.1, "프로시저 Summary 생성", "progress", progress, {"completed": completed, "total": total})
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    yield emit_phase_event(2.1, "프로시저 Summary 생성", "completed", 100, {"procedures": total})

