"""AST 그래프 생성 Phase (Phase 1) - DBMS

dbms_analyzer.py에서 분리된 Phase 1 로직입니다.
모든 로직은 100% 보존되며, 위치만 변경되었습니다.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, List

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from analyzer.strategy.dbms.ast_processor import DbmsAstProcessor
from analyzer.strategy.dbms.ddl_phase import resolve_default_schema
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
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
    
    Args:
        analyzer: DbmsAnalyzer 인스턴스 (공유 상태 접근용)
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
                # name_case 옵션 가져오기
                name_case = getattr(orchestrator, 'name_case', 'original')
                
                # 파일 경로 기반 기본 스키마 결정 (name_case 적용)
                default_schema = resolve_default_schema(
                    ctx.directory,
                    analyzer._ddl_schemas,
                    name_case
                )
                
                # AST JSON에서 last_line 추출 (children의 최대 endLine)
                last_line = 0
                for child in ctx.ast_data.get('children', []):
                    end = child.get('endLine', 0)
                    if end > last_line:
                        last_line = end
                
                processor = DbmsAstProcessor(
                    antlr_data=ctx.ast_data,
                    directory=ctx.directory,
                    file_name=ctx.file_name,
                    api_key=orchestrator.api_key,
                    locale=orchestrator.locale,
                    dbms=orchestrator.target,
                    last_line=last_line,
                    default_schema=default_schema,
                    ddl_table_metadata=analyzer._ddl_table_metadata,
                    name_case=name_case,
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

    # 결과 수신 및 스트리밍
    while completed < total:
        result = await asyncio.wait_for(results_queue.get(), timeout=300.0)
        result_type = result.get("type", "")
        
        completed += 1
        stats.files_completed = completed
        
        # Phase 1 진행률 계산 (0-50% 범위 사용)
        phase1_progress = int(completed / total * 50)
        
        if result_type == "error":
            yield emit_message(f"   ❌ [{completed}/{total}] {result['file']}: {result['message'][:50]}")
            stats.mark_file_failed(result['file'], "Phase1 실패")
            yield emit_phase_event(
                phase_num=1,
                phase_name="AST 구조 분석",
                status="in_progress",
                progress=phase1_progress,
                details={"file": result['file'], "status": "failed", "completed": completed, "total": total}
            )
        else:
            stats.add_graph_result(result["graph"], is_static=True)
            
            graph = result["graph"]
            node_count = result.get("node_count", 0)
            rel_count = result.get("rel_count", 0)
            
            # 노드 타입별 상세 집계
            node_types = {}
            for node in graph.get("Nodes", []):
                labels = node.get("Labels", [])
                for label in labels:
                    node_types[label] = node_types.get(label, 0) + 1
            
            # 상세 메시지 생성
            yield emit_message(f"   ✓ [{completed}/{total}] {result['file']}")
            
            if node_types:
                # 주요 노드 타입 표시
                proc_count = node_types.get("PROCEDURE", 0) + node_types.get("FUNCTION", 0)
                stmt_count = sum(v for k, v in node_types.items() if k in ["SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"])
                table_refs = node_types.get("Table", 0)
                
                detail_parts = []
                if proc_count:
                    detail_parts.append(f"프로시저/함수 {proc_count}개")
                if stmt_count:
                    detail_parts.append(f"SQL문 {stmt_count}개")
                if table_refs:
                    detail_parts.append(f"테이블 참조 {table_refs}개")
                
                if detail_parts:
                    yield emit_message(f"      → {', '.join(detail_parts)}")
                
                # 관계 정보
                if rel_count > 0:
                    yield emit_message(f"      → 관계 {rel_count}개 생성 (FROM, WRITES, CALLS 등)")
            
            yield emit_phase_event(
                phase_num=1,
                phase_name="AST 구조 분석",
                status="in_progress",
                progress=phase1_progress,
                details={
                    "file": result['file'],
                    "nodes": node_count,
                    "relationships": rel_count,
                    "completed": completed,
                    "total": total,
                    "node_types": node_types
                }
            )
            
            yield emit_data(
                graph=graph,
                line_number=0,
                analysis_progress=int(completed / total * 50),
                current_file=result["file"],
            )

    # 모든 작업 완료 대기
    await asyncio.gather(*tasks, return_exceptions=True)

