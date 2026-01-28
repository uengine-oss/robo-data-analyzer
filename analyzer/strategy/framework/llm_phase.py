"""LLM 분석 Phase (Phase 2) - Framework

framework_analyzer.py에서 분리된 Phase 2 로직입니다.
모든 로직은 100% 보존되며, 위치만 변경되었습니다.

핵심 로직(summary 청크 분석, User Story 생성)은
ast_processor.py의 run_llm_analysis() 내부에서 처리됩니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from analyzer.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
    format_graph_result,
)
from util.text_utils import log_process


async def run_phase2(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 2: Phase1 성공 파일의 LLM 분석을 병렬로 실행합니다.
    
    Phase1 실패 파일은 이미 필터링되어 전달되지 않음 (토큰 절감).
    
    핵심: ctx.processor.run_llm_analysis()를 호출하여 모든 LLM 분석 수행
    - summary 청크 분할/통합 (_process_unit_summaries)
    - User Story 생성 (analyze_user_story)
    
    위 로직들은 모두 ast_processor.py에 있으며, 이 함수에서는 호출만 담당.
    
    Args:
        analyzer: FrameworkAnalyzer 인스턴스 (공유 상태 접근용)
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

    async def analyze_file(ctx: FileAnalysisContext):
        async with analyzer._file_semaphore:
            try:
                if not ctx.processor:
                    raise RuntimeError(f"Phase 1에서 프로세서 초기화 실패: {ctx.file_name}")
                
                # LLM 분석 실행 (튜플 반환: queries, failed_batch_count, failed_details)
                # 이 호출 내에서 summary 청크 분석, User Story 생성이 모두 처리됨
                analysis_queries, failed_batch_count, failed_details = await ctx.processor.run_llm_analysis()
                
                if analysis_queries:
                    all_nodes = {}
                    all_relationships = {}
                    async with analyzer._cypher_lock:
                        async for batch_result in client.run_graph_query(analysis_queries):
                            for node in batch_result.get("Nodes", []):
                                all_nodes[node["Node ID"]] = node
                            for rel in batch_result.get("Relationships", []):
                                all_relationships[rel["Relationship ID"]] = rel
                            # 배치 진행률 스트리밍 (그래프 데이터 포함)
                            await results_queue.put({
                                "type": "batch_progress",
                                "file": ctx.file_name,
                                "batch": batch_result.get("batch", 0),
                                "total_batches": batch_result.get("total_batches", 0),
                                "graph": {
                                    "Nodes": batch_result.get("Nodes", []),
                                    "Relationships": batch_result.get("Relationships", []),
                                },
                            })
                    
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
                else:
                    ctx.status = FileStatus.PH2_OK
                    await results_queue.put({
                        "type": "success",
                        "file": ctx.file_name,
                        "graph": {"Nodes": [], "Relationships": []},
                        "query_count": 0,
                        "failed_batches": failed_batch_count,
                    })
                
                # 배치 실패가 있으면 즉시 중단 - 부분 실패 허용 안함
                if failed_batch_count > 0:
                    raise RuntimeError(f"{ctx.file_name}: {failed_batch_count}개 배치 실패")
                    
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 2 오류 ({ctx.file_name}): {e}", logging.ERROR, e)
                ctx.status = FileStatus.PH2_FAIL
                ctx.error_message = str(e)[:100]
                await results_queue.put({
                    "type": "error",
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise  # 즉시 중단 - 부분 실패 허용 안함

    # 모든 파일 병렬 처리 시작
    tasks = [asyncio.create_task(analyze_file(ctx)) for ctx in contexts]
    
    # Phase 2 시작 이벤트
    yield emit_phase_event(2, "AI 분석", "started", 0, {"canPause": True, "files": total})

    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=600.0)
        result_type = result.get("type", "")
        
        # warning은 카운트하지 않음 (추가 정보일 뿐)
        if result_type == "warning":
            yield emit_message(f"   ⚠️ {result['file']}: {result['message']}")
            continue
        
        # 배치 진행률은 카운트하지 않음 (중간 진행 상태)
        if result_type == "batch_progress":
            batch = result.get("batch", 0)
            total_batches = result.get("total_batches", 0)
            graph = result.get("graph")
            yield emit_message(f"      📦 {result['file']}: 배치 {batch}/{total_batches} 저장 완료")
            # 배치별 그래프 데이터 즉시 전송
            if graph:
                yield emit_data(graph=graph)
            continue
        
        completed += 1
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
            stats.mark_file_failed(result['file'], "Phase2 실패")
        else:
            stats.llm_batches_executed += 1
            graph = result["graph"]
            stats.add_graph_result(graph, is_static=False)
            
            # 배치 실패 정보 표시
            failed_batches = result.get("failed_batches", 0)
            failed_details = result.get("failed_details", [])
            fail_info = f" (배치 {failed_batches}개 실패)" if failed_batches > 0 else ""
            
            graph_msg = format_graph_result(graph)
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']} (쿼리 {result['query_count']}개){fail_info}")
            if graph_msg:
                for line in graph_msg.split("\n")[:3]:
                    yield emit_message(f"      {line}")
            
            # 실패 상세 정보 출력 (최대 3개)
            if failed_details:
                stats.llm_batches_failed += len(failed_details)
                for detail in failed_details[:3]:
                    yield emit_message(f"      ⚠️ 배치 #{detail['batch_id']} ({detail['node_ranges']}): {detail['error'][:50]}")
            
            yield emit_data(
                graph=graph,
                line_number=0,
                analysis_progress=50 + int(completed / total * 50),
                current_file=result["file"],
            )
            
            # 진행률 이벤트
            progress = int(completed / total * 100)
            yield emit_phase_event(2, "AI 분석", "progress", progress, {"completed": completed, "total": total})

    # 모든 작업 완료 대기
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Phase 2 완료 이벤트
    yield emit_phase_event(2, "AI 분석", "completed", 100, {"batches": stats.llm_batches_executed})

