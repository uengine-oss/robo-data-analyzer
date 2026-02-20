"""클래스 Summary 생성 - Framework

llm_phase.py에서 분리된 Phase 2.1 로직입니다.
모든 파일의 모든 클래스를 병렬로 처리하여 클래스 summary를 생성하고 즉시 저장합니다.
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


async def run_phase2_1_class_summary(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.1: 클래스 Summary 생성 (병렬 처리)
    
    모든 파일의 모든 클래스를 병렬로 처리하여 클래스 summary를 생성하고 즉시 저장합니다.
    """
    from analyzer.strategy.framework.ast_processor import analyze_stereotype
    from analyzer.strategy.framework.ast_processor import CLASS_TYPES
    
    MAX_SUMMARY_CHUNK_TOKEN = settings.batch.max_summary_chunk_token
    
    success_contexts = [ctx for ctx in contexts 
                       if ctx.status == FileStatus.PH2_OK 
                       and ctx.processor 
                       and hasattr(ctx, 'unit_summary_store') 
                       and ctx.unit_summary_store]
    
    if not success_contexts:
        yield emit_message("   ℹ️ Phase 2.1: 처리할 파일 없음")
        return
    
    # 모든 클래스 수집
    all_classes = []
    for ctx in success_contexts:
        processor = ctx.processor
        classes = processor._unit_info
        for class_key, info in classes.items():
            summaries = ctx.unit_summary_store.get(class_key, {})
            if summaries:
                class_node = next(
                    (n for n in processor._nodes 
                     if n.start_line == info.node_start and n.node_type == info.kind),
                    None,
                )
                if class_node and class_node.ok:
                    all_classes.append((ctx, processor, class_key, info, summaries, class_node))
    
    if not all_classes:
        yield emit_message("   ℹ️ Phase 2.1: 처리할 클래스 없음")
        return
    
    total = len(all_classes)
    completed = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
    
    async def process_class(ctx: FileAnalysisContext, processor, class_key: str, info, summaries: dict, class_node):
        """단일 클래스의 summary 생성 및 저장"""
        async with semaphore:
            try:
                # Phase 2.1: 클래스 Summary 생성
                # 청크 분할
                chunks = _split_summaries_by_token(summaries, MAX_SUMMARY_CHUNK_TOKEN)
                if not chunks:
                    final_summary = ""
                else:
                    log_process("ANALYZE", "PHASE_2.1", f"📦 {info.name}: summary 청크 분할 완료 ({len(chunks)}개 청크)")
                    
                    class_meta_parts = []
                    if class_node.comment:
                        class_meta_parts.append(class_node.comment)
                    if class_node.annotations:
                        class_meta_parts.append(class_node.annotations)
                    class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""
                    
                    # 동시 처리 수 제한
                    max_concurrent = settings.concurrency.max_concurrency
                    chunk_semaphore = asyncio.Semaphore(max_concurrent)
                    
                    async def process_chunk(chunk_idx: int, chunk: dict) -> Dict[str, Any]:
                        chunk_tokens = calculate_code_token(json.dumps(chunk, ensure_ascii=False))
                        log_process("ANALYZE", "PHASE_2.1", f"  → 청크 {chunk_idx + 1}/{len(chunks)} 처리 시작 (토큰: {chunk_tokens})")
                        
                        result = await asyncio.to_thread(
                            analyze_stereotype,
                            chunk,
                            orchestrator.api_key,
                            orchestrator.locale,
                            class_meta,
                        )
                        return processor.validate_dict_result(result, "청크 분석")
                    
                    # Semaphore로 동시 처리 수 제한
                    async def process_chunk_with_limit(chunk_idx: int, chunk: dict) -> Dict[str, Any]:
                        async with chunk_semaphore:
                            return await process_chunk(chunk_idx, chunk)
                    
                    chunk_results_raw = await asyncio.gather(
                        *[process_chunk_with_limit(idx, chunk) for idx, chunk in enumerate(chunks)]
                    )
                    
                    chunk_results = [r for r in chunk_results_raw if r]
                    
                    if not chunk_results:
                        raise RuntimeError(f"{info.name}: 청크 처리 결과가 모두 비어있음")
                    
                    # 통합
                    if len(chunk_results) == 1:
                        final_summary = chunk_results[0].get('summary', '')
                    else:
                        combined_summaries = {f"CHUNK_{idx + 1}": r.get('summary', '') for idx, r in enumerate(chunk_results)}
                        result = await asyncio.to_thread(
                            analyze_stereotype,
                            combined_summaries,
                            orchestrator.api_key,
                            orchestrator.locale,
                            class_meta,
                        )
                        validated = processor.validate_dict_result(result, "청크 통합")
                        final_summary = validated.get('summary', '')
                    
                    log_process("ANALYZE", "PHASE_2.1", f"✅ {info.name}: 클래스 summary 생성 완료")
                
                if not final_summary:
                    await results_queue.put({
                        "type": "success",
                        "class": info.name,
                        "file": ctx.file_name,
                        "queries": [],
                    })
                    return
                
                # 즉시 저장 (클래스 summary만)
                queries = [{
                    "query": (
                        f"MATCH (__cy_n__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                        f"SET __cy_n__.summary = $summary\n"
                        f"RETURN __cy_n__"
                    ),
                    "params": {
                        "start_line": info.node_start,
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
                
                # final_summary를 ctx에 저장 (Phase 2.2, 2.3에서 사용)
                if not hasattr(ctx, 'class_summaries'):
                    ctx.class_summaries = {}
                ctx.class_summaries[class_key] = {
                    "final_summary": final_summary,
                    "class_node": class_node,
                }
                
                await results_queue.put({
                    "type": "success",
                    "class": info.name,
                    "file": ctx.file_name,
                    "queries": queries,
                })
                
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2.1 오류 ({ctx.file_name}/{info.name}): {e}", logging.ERROR, e)
                await results_queue.put({
                    "type": "error",
                    "class": info.name,
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise
    
    # Phase 2.1 시작 이벤트
    yield emit_phase_event(2.1, "클래스 Summary 생성", "started", 0, {"canPause": True, "classes": total})
    
    # 모든 클래스 병렬 처리
    tasks = [asyncio.create_task(process_class(ctx, proc, ck, inf, summ, cn)) 
             for ctx, proc, ck, inf, summ, cn in all_classes]
    
    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['class']}: {result['message'][:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['class']}: summary 생성")
            
            progress = int(completed / total * 100)
            yield emit_phase_event(2.1, "클래스 Summary 생성", "progress", progress, {"completed": completed, "total": total})
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    yield emit_phase_event(2.1, "클래스 Summary 생성", "completed", 100, {"classes": total})

