"""LLM 분석 Phase (Phase 2) - Framework

framework_analyzer.py에서 분리된 Phase 2 오케스트레이터입니다.

핵심 로직(summary + stereotype + DEPENDENCY/CALLS/USES 관계 생성)은
ast_processor.py의 run_llm_analysis() 내부에서 처리됩니다.

각 Phase는 별도 파일로 분리되어 있습니다:
- method_enhancement.py: 메서드 Summary 보강
- class_summary.py: 클래스 Summary 생성
- stereotype.py: 스테레오타입 판정
- user_story.py: User Story 생성

배치 완료 시 즉시 summary_vec 벡터화.
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, List

from openai import AsyncOpenAI
from httpx import Timeout

from client.neo4j_client import Neo4jClient
from client.embedding_client import EmbeddingClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.base.file_context import FileStatus, FileAnalysisContext
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
    
    # 타임아웃 설정 (연결: 120초, 읽기: 300초)
    timeout = Timeout(120.0, connect=120.0, read=300.0)
    openai_client = AsyncOpenAI(
        api_key=api_key,
        timeout=timeout,
        max_retries=3
    )
    embedding_client = EmbeddingClient(openai_client)
    
    # summary가 있고 summary_vec이 없는 노드 조회
    # 벡터화 대상 노드: METHOD / CONSTRUCTOR / FIELD
    query = """
    MATCH (__cy_n__)
    WHERE (__cy_n__:METHOD OR __cy_n__:CONSTRUCTOR OR __cy_n__:FIELD)
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
    
    # 배치 처리 (30개씩)
    batch_size = 30
    vectorized_count = 0
    
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        texts = [item.get("summary", "") for item in batch]
        
        embed_start = time.time()
        vectors = await embedding_client.embed_batch(texts)
        embed_time = time.time() - embed_start
        
        log_process("VECTORIZE", "SUMMARY", f"{file_name}: {len(vectors)}개 노드 임베딩 완료 ({embed_time:.2f}초)")
        
        # UNWIND로 한번에 저장
        if not vectors:
            continue
            
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
    
    실행 단계:
    1. Phase 2: 모든 파일의 LLM 배치 분석 (기본 summary 생성)
    2. Phase 2.0: 전체 그래프 메서드 summary 보강
    3. Phase 2.1~2.4: 각 파일의 단위 summary 처리 (클래스 summary, 스테레오타입, 유저스토리)
    
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
                
                # 배치별 그래프 누적용 (통계 집계에 사용)
                all_nodes = {}
                all_relationships = {}
                
                async def on_queries_ready(queries, batch_id, total_batches):
                    """LLM 배치 완료 즉시 Neo4j 저장 + 프론트 스트리밍"""
                    if not queries:
                        return
                    async with analyzer._cypher_lock:
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
                
                # Phase 2: LLM 배치 분석 (기본 summary 생성)
                analysis_queries, failed_batch_count, failed_details, unit_summary_store = await ctx.processor.run_llm_analysis(
                    on_queries_ready=on_queries_ready,
                )
                # unit_summary_store를 ctx에 저장 (Phase 2.0 후 사용)
                ctx.unit_summary_store = unit_summary_store
                
                graph = {"Nodes": list(all_nodes.values()), "Relationships": list(all_relationships.values())}
                
                # Phase 2.5: 즉시 벡터화 (모든 배치 완료 후)
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
        
        # 배치 진행률은 카운트하지 않음 (중간 진행 상태) — 배치별 즉시 스트리밍
        if result_type == "batch_progress":
            batch_id = result.get("batch_id", 0)
            total_batches = result.get("total_batches", 0)
            graph = result.get("graph")
            node_count = len(graph.get("Nodes", [])) if graph else 0
            batch_label = f"#{batch_id}/{total_batches}" if total_batches > 0 else f"#{batch_id}"
            yield emit_message(f"│     📦 배치 {batch_label} 저장 완료 (노드 {node_count}개)")
            # 배치별 그래프 데이터 즉시 전송
            if graph:
                yield emit_data(graph=graph, current_file=result.get("file"))
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
            
            # 그래프 데이터는 이미 배치별로 전송 완료 — 진행률만 전송
            yield emit_data(
                analysis_progress=phase2_progress,
                current_file=result["file"],
            )

    # 모든 작업 완료 대기
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Phase 2.0: 메서드 Summary 보강 (전체 그래프, 모든 파일 완료 후)
    from analyzer.strategy.framework.method_enhancement import run_phase2_0_enhancement
    async for chunk in run_phase2_0_enhancement(analyzer, contexts, client, orchestrator, stats):
        yield chunk
    
    # Phase 2.1: 클래스 Summary 생성 (병렬)
    from analyzer.strategy.framework.class_summary import run_phase2_1_class_summary
    async for chunk in run_phase2_1_class_summary(analyzer, contexts, client, orchestrator, stats):
        yield chunk
    
    # Phase 2.2: 스테레오타입 판정 (병렬)
    from analyzer.strategy.framework.stereotype import run_phase2_2_stereotype
    async for chunk in run_phase2_2_stereotype(analyzer, contexts, client, orchestrator, stats):
        yield chunk
    
    # Phase 2.3: User Story 생성 (병렬)
    from analyzer.strategy.framework.user_story import run_phase2_3_user_story
    async for chunk in run_phase2_3_user_story(analyzer, contexts, client, orchestrator, stats):
        yield chunk
    
    # Phase 2 완료 이벤트
    yield emit_phase_event(2, "AI 분석", "completed", 100, {"batches": stats.llm_batches_executed})



