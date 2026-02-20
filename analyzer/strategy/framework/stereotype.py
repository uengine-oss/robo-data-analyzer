"""스테레오타입 판정 - Framework

llm_phase.py에서 분리된 Phase 2.2 로직입니다.
모든 파일의 모든 클래스를 병렬로 처리하여 스테레오타입을 판정하고 즉시 저장합니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from config.settings import settings
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import log_process


async def run_phase2_2_stereotype(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2.2: 스테레오타입 판정 (병렬 처리)
    
    모든 파일의 모든 클래스를 병렬로 처리하여 스테레오타입을 판정하고 즉시 저장합니다.
    """
    from analyzer.strategy.framework.ast_processor import analyze_stereotype
    from analyzer.strategy.framework.ast_processor import METHOD_TYPES
    
    # Phase 2.1에서 생성한 클래스 summary가 있는 클래스만 처리
    all_classes = []
    for ctx in contexts:
        if (ctx.status == FileStatus.PH2_OK 
            and ctx.processor 
            and hasattr(ctx, 'class_summaries') 
            and ctx.class_summaries):
            for class_key, summary_data in ctx.class_summaries.items():
                info = ctx.processor._unit_info.get(class_key)
                if info:
                    all_classes.append((ctx, ctx.processor, class_key, info, summary_data))
    
    if not all_classes:
        yield emit_message("   ℹ️ Phase 2.2: 처리할 클래스 없음")
        return
    
    total = len(all_classes)
    completed = 0
    results_queue: asyncio.Queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(settings.concurrency.max_concurrency)
    
    async def process_class(ctx: FileAnalysisContext, processor, class_key: str, info, summary_data: dict):
        """단일 클래스의 스테레오타입 판정 및 저장"""
        async with semaphore:
            try:
                final_summary = summary_data["final_summary"]
                class_node = summary_data["class_node"]
                
                # Phase 2.2: 스테레오타입 판정
                if not final_summary:
                    stereotype_result = {"class_stereotype": "", "method_stereotypes": {}}
                else:
                    class_meta_parts = []
                    if class_node.comment:
                        class_meta_parts.append(class_node.comment)
                    if class_node.annotations:
                        class_meta_parts.append(class_node.annotations)
                    class_meta = '\n'.join(class_meta_parts) if class_meta_parts else ""
                    
                    # 최종 summary로 스테레오타입 재분석
                    result = await asyncio.to_thread(
                        analyze_stereotype,
                        {"FINAL_SUMMARY": final_summary},
                        orchestrator.api_key,
                        orchestrator.locale,
                        class_meta,
                    )
                    validated = processor.validate_dict_result(result, "스테레오타입 판정")
                    
                    class_stereotype = validated.get('classStereotype', '')
                    method_stereotypes = validated.get('methodStereotypes', {})
                    
                    log_process("ANALYZE", "PHASE_2.2", f"✅ {info.name}: 스테레오타입 판정 완료")
                    if class_stereotype:
                        log_process("ANALYZE", "STEREOTYPE", f"   🏷️ {info.name}: {class_stereotype}")
                    
                    stereotype_result = {
                        "class_stereotype": class_stereotype,
                        "method_stereotypes": method_stereotypes,
                    }
                
                class_stereotype = stereotype_result.get("class_stereotype", "")
                method_stereotypes = stereotype_result.get("method_stereotypes", {})
                
                queries = []
                
                # 클래스 스테레오타입 저장
                if class_stereotype:
                    queries.append({
                        "query": (
                            f"MATCH (__cy_n__:{info.kind} {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                            f"SET __cy_n__.stereotype = $stereotype, __cy_n__:{class_stereotype}\n"
                            f"RETURN __cy_n__"
                        ),
                        "params": {
                            "start_line": info.node_start,
                            "directory": processor.full_directory,
                            "file_name": processor.file_name,
                            "stereotype": class_stereotype,
                        }
                    })
                
                # 메서드 스테레오타입 저장
                for method_name, method_st in method_stereotypes.items():
                    if not method_st:
                        continue
                    method_node = next(
                        (n for n in processor._nodes 
                         if n.node_type in METHOD_TYPES 
                         and n.unit_key == class_key
                         and n.signature 
                         and method_name in n.signature),
                        None,
                    )
                    if method_node:
                        queries.append({
                            "query": (
                                f"MATCH (__cy_m__:METHOD {{start_line: $start_line, directory: $directory, file_name: $file_name}})\n"
                                f"SET __cy_m__.stereotype = $stereotype, __cy_m__:{method_st}\n"
                                f"RETURN __cy_m__"
                            ),
                            "params": {
                                "start_line": method_node.start_line,
                                "directory": processor.full_directory,
                                "file_name": processor.file_name,
                                "stereotype": method_st,
                            }
                        })
                
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
                
                # stereotype_result를 ctx에 저장 (Phase 2.3에서 사용)
                if not hasattr(ctx, 'stereotype_results'):
                    ctx.stereotype_results = {}
                ctx.stereotype_results[class_key] = stereotype_result
                
                await results_queue.put({
                    "type": "success",
                    "class": info.name,
                    "file": ctx.file_name,
                    "queries": queries,
                })
                
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2.2 오류 ({ctx.file_name}/{info.name}): {e}", logging.ERROR, e)
                await results_queue.put({
                    "type": "error",
                    "class": info.name,
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise
    
    # Phase 2.2 시작 이벤트
    yield emit_phase_event(2.2, "스테레오타입 판정", "started", 0, {"canPause": True, "classes": total})
    
    # 모든 클래스 병렬 처리
    tasks = [asyncio.create_task(process_class(ctx, proc, ck, inf, sd)) 
             for ctx, proc, ck, inf, sd in all_classes]
    
    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}/{result['class']}: {result['message'][:50]}")
        else:
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}/{result['class']}: 스테레오타입 판정")
            
            progress = int(completed / total * 100)
            yield emit_phase_event(2.2, "스테레오타입 판정", "progress", progress, {"completed": completed, "total": total})
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    yield emit_phase_event(2.2, "스테레오타입 판정", "completed", 100, {"classes": total})

