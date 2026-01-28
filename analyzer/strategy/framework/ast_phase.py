"""AST 그래프 생성 Phase (Phase 1) - Framework

framework_analyzer.py에서 분리된 Phase 1 로직입니다.
모든 로직은 100% 보존되며, 위치만 변경되었습니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from analyzer.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from analyzer.strategy.framework.ast_processor import FrameworkAstProcessor
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
    format_graph_result,
)
from util.text_utils import log_process


async def run_phase1(
    analyzer: Any,
    contexts: List[FileAnalysisContext],
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 1: 모든 파일의 AST 그래프를 병렬로 생성합니다.
    
    파일별 상태 기록:
    - 성공: PH1_OK → Phase 2 진행
    - 실패: PH1_FAIL → Phase 2 스킵 (토큰 절감)
    
    Args:
        analyzer: FrameworkAnalyzer 인스턴스 (공유 상태 접근용)
        contexts: 파일 컨텍스트 리스트
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
    """
    completed = 0
    total = len(contexts)
    results_queue: asyncio.Queue = asyncio.Queue()

    async def process_file(ctx: FileAnalysisContext):
        async with analyzer._file_semaphore:
            try:
                # AST JSON에서 last_line 추출 (children의 최대 endLine)
                last_line = 0
                for child in ctx.ast_data.get('children', []):
                    end = child.get('endLine', 0)
                    if end > last_line:
                        last_line = end
                
                processor = FrameworkAstProcessor(
                    antlr_data=ctx.ast_data,
                    directory=ctx.directory,
                    file_name=ctx.file_name,
                    api_key=orchestrator.api_key,
                    locale=orchestrator.locale,
                    last_line=last_line,
                )
                ctx.processor = processor
                
                # 정적 그래프 생성
                queries = processor.build_static_graph_queries()
                
                if queries:
                    all_nodes = {}
                    all_relationships = {}
                    async with analyzer._cypher_lock:
                        async for batch_result in client.run_graph_query(queries):
                            for node in batch_result.get("Nodes", []):
                                all_nodes[node["Node ID"]] = node
                            for rel in batch_result.get("Relationships", []):
                                all_relationships[rel["Relationship ID"]] = rel
                    
                    graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
                    node_count = len(graph.get("Nodes", []))
                    rel_count = len(graph.get("Relationships", []))
                    
                    ctx.status = FileStatus.PH1_OK
                    await results_queue.put({
                        "type": "success",
                        "file": ctx.file_name,
                        "graph": graph,
                        "node_count": node_count,
                        "rel_count": rel_count,
                    })
                else:
                    ctx.status = FileStatus.PH1_OK
                    await results_queue.put({
                        "type": "success",
                        "file": ctx.file_name,
                        "graph": {"Nodes": [], "Relationships": []},
                        "node_count": 0,
                        "rel_count": 0,
                    })
                    
            except Exception as e:
                log_process("ANALYZE", "ERROR", f"Phase 1 오류 ({ctx.file_name}): {e}", logging.ERROR, e)
                ctx.status = FileStatus.PH1_FAIL
                ctx.error_message = str(e)[:100]
                await results_queue.put({
                    "type": "error",
                    "file": ctx.file_name,
                    "message": str(e),
                })
                raise  # 즉시 중단 - 부분 실패 허용 안함

    # 모든 파일 병렬 처리 시작
    tasks = [asyncio.create_task(process_file(ctx)) for ctx in contexts]
    
    # Phase 1 시작 이벤트
    yield emit_phase_event(1, "AST 구조 생성", "started", 0, {"canPause": True, "files": total})

    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        stats.files_completed = completed
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
            stats.mark_file_failed(result['file'], "Phase1 실패")
        else:
            stats.add_graph_result(result["graph"], is_static=True)
            
            graph = result["graph"]
            graph_msg = format_graph_result(graph)
            
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}")
            if graph_msg:
                for line in graph_msg.split("\n")[:3]:  # 최대 3줄
                    yield emit_message(f"      {line}")
            
            yield emit_data(
                graph=graph,
                line_number=0,
                analysis_progress=int(completed / total * 50),
                current_file=result["file"],
            )
            
            # 진행률 이벤트
            progress = int(completed / total * 100)
            yield emit_phase_event(1, "AST 구조 생성", "progress", progress, {"completed": completed, "total": total})

    # 모든 작업 완료 대기
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Phase 1 완료 이벤트
    yield emit_phase_event(1, "AST 구조 생성", "completed", 100, {"nodes": stats.static_nodes_created})

