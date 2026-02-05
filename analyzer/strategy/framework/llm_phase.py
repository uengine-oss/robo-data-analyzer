"""LLM 분석 Phase (Phase 2) - Framework

framework_analyzer.py에서 분리된 Phase 2 로직입니다.
모든 로직은 100% 보존되며, 위치만 변경되었습니다.

핵심 로직(summary 청크 분석, User Story 생성)은
ast_processor.py의 run_llm_analysis() 내부에서 처리됩니다.

Phase 2.5: 배치 완료 시 즉시 summary_vec 벡터화
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, List

from openai import AsyncOpenAI

from client.neo4j_client import Neo4jClient
from client.embedding_client import EmbeddingClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
from config.settings import settings
from util.stream_event import (
    emit_data,
    emit_message,
    emit_phase_event,
)
from util.text_utils import log_process


async def vectorize_summaries(
    analyzer: Any,
    client: Neo4jClient,
    api_key: str,
    file_name: str,
) -> int:
    """파일의 모든 노드 summary를 벡터화합니다.
    
    Args:
        analyzer: FrameworkAnalyzer 인스턴스
        client: Neo4j 클라이언트
        api_key: OpenAI API 키
        file_name: 대상 파일명
        
    Returns:
        벡터화된 노드 수
    """
    if not api_key:
        return 0
    
    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)
    
    # summary가 있고 summary_vec이 없는 노드 조회
    query = """
    MATCH (__cy_n__)
    WHERE (__cy_n__:CLASS OR __cy_n__:INTERFACE OR __cy_n__:METHOD OR __cy_n__:FIELD OR __cy_n__:ENUM)
      AND __cy_n__.file_name = $file_name
      AND __cy_n__.summary IS NOT NULL
      AND __cy_n__.summary <> ''
      AND (__cy_n__.summary_vec IS NULL OR size(__cy_n__.summary_vec) = 0)
    RETURN elementId(__cy_n__) AS nid, 
           __cy_n__.summary AS summary,
           labels(__cy_n__)[0] AS node_type
    """
    
    async with analyzer._cypher_lock:
        result = await client.execute_with_params(query, {"file_name": file_name})
    
    nodes = result if result else []
    if not nodes:
        return 0
    
    # 배치 처리 (50개씩)
    batch_size = 50
    vectorized_count = 0
    
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        texts = [item.get("summary", "") for item in batch]
        
        embed_start = time.time()
        vectors = await embedding_client.embed_batch(texts)
        embed_time = time.time() - embed_start
        
        log_process("VECTORIZE", "SUMMARY", f"{file_name}: {len(vectors)}개 노드 임베딩 완료 ({embed_time:.2f}초)")
        
        # UNWIND로 한번에 저장
        vector_updates = []
        for item, vector in zip(batch, vectors):
            if vector:
                vector_updates.append({
                    "nid": item['nid'],
                    "vector": vector
                })
                vectorized_count += 1
        
        if vector_updates:
            update_query = """
            UNWIND $items AS item
            MATCH (__cy_n__) WHERE elementId(__cy_n__) = item.nid
            SET __cy_n__.summary_vec = item.vector
            RETURN __cy_n__
            """
            async with analyzer._cypher_lock:
                await client.execute_with_params(update_query, {"items": vector_updates})
    
    return vectorized_count


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
    
    # 시작 메시지 - 전체 현황 표시
    max_workers = analyzer._file_semaphore._value if hasattr(analyzer._file_semaphore, '_value') else 5
    yield emit_message(f"")
    yield emit_message(f"┌─────────────────────────────────────────────────────────┐")
    yield emit_message(f"│  📊 Phase 2: AI 분석 시작                                │")
    yield emit_message(f"│     총 {total}개 파일, 동시 처리: {max_workers}개                         │")
    yield emit_message(f"└─────────────────────────────────────────────────────────┘")

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
                    
                    # Phase 2.5: 즉시 벡터화 (배치 완료 후)
                    vectorized = await vectorize_summaries(
                        analyzer, client, orchestrator.api_key, ctx.file_name
                    )
                    if vectorized > 0:
                        log_process("VECTORIZE", "SUMMARY", f"✅ {ctx.file_name}: {vectorized}개 노드 summary_vec 저장 완료")
                    
                    ctx.status = FileStatus.PH2_OK
                    await results_queue.put({
                        "type": "success",
                        "file": ctx.file_name,
                        "graph": graph,
                        "query_count": len(analysis_queries),
                        "failed_batches": failed_batch_count,
                        "failed_details": failed_details,
                        "vectorized": vectorized,
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
            node_count = len(graph.get("Nodes", [])) if graph else 0
            yield emit_message(f"│     📦 배치 {batch}/{total_batches} 저장 (노드 {node_count}개)")
            # 배치별 그래프 데이터 즉시 전송
            if graph:
                yield emit_data(graph=graph)
            continue
        
        completed += 1
        remaining = total - completed
        
        if result_type == "error":
            yield emit_message(f"├─ ❌ [{completed}/{total}] {result['file']}")
            yield emit_message(f"│     오류: {result['message'][:80]}")
            yield emit_message(f"│     남은 파일: {remaining}개")
            stats.mark_file_failed(result['file'], "Phase2 실패")
        else:
            stats.llm_batches_executed += 1
            graph = result["graph"]
            stats.add_graph_result(graph, is_static=False)
            
            # 배치 실패 정보 표시
            failed_batches = result.get("failed_batches", 0)
            failed_details = result.get("failed_details", [])
            
            # 분석 결과 상세 집계
            node_count = len(graph.get("Nodes", []))
            rel_count = len(graph.get("Relationships", []))
            
            # 상태 아이콘 선택
            status_icon = "✅" if failed_batches == 0 else "⚠️"
            
            # 파일 완료 메시지 (트리 구조)
            vectorized = result.get("vectorized", 0)
            yield emit_message(f"├─ {status_icon} [{completed}/{total}] {result['file']}")
            yield emit_message(f"│     쿼리: {result['query_count']}개, 노드: {node_count}개, 관계: {rel_count}개, 벡터: {vectorized}개")
            
            if remaining > 0:
                yield emit_message(f"│     남은 파일: {remaining}개")
            
            # 실패 상세 정보 출력 (최대 3개)
            if failed_details:
                stats.llm_batches_failed += len(failed_details)
                for detail in failed_details[:3]:
                    yield emit_message(f"│     ⚠️ 배치 #{detail['batch_id']}: {detail['error'][:50]}")
            
            # Phase 2 진행률 계산 (50-100% 범위 사용)
            phase2_progress = 50 + int(completed / total * 50)
            
            yield emit_phase_event(
                phase_num=2,
                phase_name="AI 분석",
                status="in_progress",
                progress=phase2_progress,
                details={
                    "file": result['file'],
                    "queries": result['query_count'],
                    "nodes_updated": node_count,
                    "relationships_updated": rel_count,
                    "completed": completed,
                    "total": total
                }
            )
            
            yield emit_data(
                graph=graph,
                line_number=0,
                analysis_progress=phase2_progress,
                current_file=result["file"],
            )

    # 모든 작업 완료 대기
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Phase 2 완료 이벤트
    yield emit_phase_event(2, "AI 분석", "completed", 100, {"batches": stats.llm_batches_executed})

