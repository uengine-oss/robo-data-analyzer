"""자율 에이전트 워크플로우

비타겟 파일 분석을 Main Agent가 자율적으로 수행합니다.
고정된 단계 순서가 아닌, 에이전트가 프롬프트 지침에 따라 TODO를 작성하고 실행합니다.

[변경됨] 에이전트 1회 생성 → 모든 파일에 재사용
- Main Agent는 외부에서 주입 (nontarget_phase.py에서 1회 생성)
- 파일별 동적 데이터는 task 메시지(HumanMessage)로 전달
- 메모리를 직접 read_memory/write_memory로 자율 관리

RuntimeCtx 패턴:
- 비직렬화 객체(client, lock) 관리
- 모든 도구에 ctx 전달
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from langchain_core.messages import HumanMessage
from openai import AsyncOpenAI

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import (
    log_nontarget,
    log_workflow_state,
)
from analyzer.strategy.framework.nontarget.agents.deepagents_client import (
    DeepAgentsClient,
    _extract_agent_output,
    _parse_json_from_output,
)
from analyzer.strategy.framework.nontarget.utils.trace_logger import TraceLogger
from client.embedding_client import EmbeddingClient
from config.settings import settings


# ============================================================================
# State 정의 (결과 저장용)
# ============================================================================

class NontargetState(TypedDict, total=False):
    """워크플로우 결과 상태"""
    file_path: str
    blocks: List[Dict[str, Any]]
    summaries: List[Dict[str, Any]]
    status: str  # success, partial, failed
    error: Optional[str]
    memory_updates: List[Dict[str, Any]]
    corrections_made: List[Dict[str, Any]]


# ============================================================================
# Runtime Context (직렬화 불가능한 객체)
# ============================================================================

@dataclass
class RuntimeCtx:
    """런타임 컨텍스트
    
    Neo4j 클라이언트, 락, 트레이스 로거 등을 담습니다.
    모든 도구에 주입됩니다.
    """
    client: Any = None
    cypher_lock: Optional[asyncio.Lock] = None
    trace_logger: Optional[TraceLogger] = None


# ============================================================================
# 자율 에이전트 실행
# ============================================================================

async def run_autonomous_agent(
    file_path: str,
    ctx: RuntimeCtx,
    main_agent: Any,
    deepagents_client: DeepAgentsClient,
    enable_tracing: bool = True,
) -> NontargetState:
    """Main Agent가 자율적으로 파일 분석 수행
    
    에이전트는 외부에서 1회 생성되어 주입됩니다.
    파일별 동적 데이터는 task 메시지(HumanMessage)로 전달됩니다.
    
    500줄씩 순차 처리:
    1. 파일을 500줄 단위로 분할
    2. 각 청크마다 task 메시지 렌더링 후 에이전트 호출
    3. pattern_memory 축적하여 다음 청크로 전달
    4. 최종 규칙 확정 및 블록화
    
    Args:
        file_path: 분석 대상 파일 경로
        ctx: RuntimeCtx (client, cypher_lock)
        main_agent: 사전 생성된 메인 에이전트 (서브에이전트 포함)
        deepagents_client: task 메시지 렌더링용 클라이언트
        enable_tracing: 트레이스 로깅 활성화
        
    Returns:
        분석 결과 상태
    """
    from analyzer.strategy.framework.nontarget.processors.chunk_processor import extract_chunks_for_analysis
    
    # TraceLogger 초기화 (파일마다 새로운 트레이스)
    if enable_tracing:
        ctx.trace_logger = TraceLogger(file_path)
    
    log_nontarget("WORKFLOW", f"자율 에이전트 실행 시작: {file_path}")
    
    # 워크플로우 시작 로깅
        if ctx.trace_logger:
        ctx.trace_logger.log_workflow_start(file_path, {"mode": "autonomous_sequential"})
    
    # 파일 기본 정보
    file_name = os.path.basename(file_path)
    file_ext = os.path.splitext(file_name)[1].lower()
    
    # 파일을 500줄씩 분할
    try:
        chunks = extract_chunks_for_analysis(file_path, chunk_lines=500)
    except Exception as e:
        log_nontarget("WORKFLOW", f"파일 분할 실패: {e}")
        return {
            "file_path": file_path,
            "blocks": [],
            "summaries": [],
            "status": "failed",
            "error": str(e),
            "memory_updates": [],
            "corrections_made": [],
        }
    
    total_chunks = len(chunks)
    line_count = chunks[-1]["line_end"] if chunks else 0
    log_nontarget("WORKFLOW", f"파일 분할 완료: {total_chunks}개 청크 ({line_count} lines)")
    
    # 순차 처리
    pattern_memory = ""
    all_blocks: List[Dict[str, Any]] = []
    all_summaries: List[Dict[str, Any]] = []
    all_memory_updates: List[Dict[str, Any]] = []
    all_corrections: List[Dict[str, Any]] = []
    
    for chunk in chunks:
        chunk_idx = chunk["chunk_idx"]
        log_nontarget("WORKFLOW", f"청크 {chunk_idx + 1}/{total_chunks} 분석 시작")
            
        # 입력값 준비 (task 메시지 렌더링용)
        inputs = {
            "file_path": file_path,
            "file_name": file_name,
            "file_ext": file_ext,
            "line_count": line_count,
            "file_content": chunk["text"],
            "chunk_idx": chunk_idx,
            "total_chunks": total_chunks,
            "pattern_memory": pattern_memory,
        }
        
        # YAML task 필드에서 분석 요청 메시지 렌더링
        # (에이전트 재생성 없이 task 메시지만 변경)
        task_message = deepagents_client.get_rendered_task("agents/main", inputs)
        
        try:
            # Main Agent 실행 (사전 생성된 에이전트 재사용)
            result = await main_agent.ainvoke({
                "messages": [HumanMessage(content=task_message)]
            })
            
            # 결과 파싱
            output = _extract_agent_output(result)
            
            if ctx.trace_logger:
                ctx.trace_logger.log_agent_call(
                    "main_agent", 
                    f"청크 {chunk_idx + 1}/{total_chunks}", 
                    output[:1000]
                )
            
            # JSON 파싱 시도
            try:
                parsed = _parse_json_from_output(output)
                
                # 블록/요약 축적
                blocks = parsed.get("blocks", [])
                summaries = parsed.get("summaries", blocks)
                all_blocks.extend(blocks)
                all_summaries.extend(summaries)
        
                # 메모리 업데이트 축적
                all_memory_updates.extend(parsed.get("memory_updates", []))
                all_corrections.extend(parsed.get("corrections_made", []))
                
                # 패턴 메모리 업데이트 (다음 청크로 전달)
                pattern_memory = parsed.get("pattern_memory", pattern_memory)
                
                log_nontarget("WORKFLOW", f"청크 {chunk_idx + 1}/{total_chunks} 완료: {len(blocks)}개 블록")
                
            except Exception as e:
                log_nontarget("WORKFLOW", f"청크 {chunk_idx + 1} JSON 파싱 실패: {e}")
                
        except Exception as e:
            log_nontarget("WORKFLOW", f"청크 {chunk_idx + 1} 에이전트 실행 오류: {e}")
        if ctx.trace_logger:
                ctx.trace_logger.log_error("workflow", str(e), {"chunk_idx": chunk_idx})
    
    log_nontarget("WORKFLOW", f"전체 분석 완료: {len(all_blocks)}개 블록")
    
    # [아키텍처 B 검증]
    # Main Agent가 서브에이전트에게 작업 위임하고, 서브 결과를 받아서
    # 메인에이전트가 직접 Neo4j에 저장합니다.
    # 따라서 메인 결과에 blocks[].id가 반드시 있어야 합니다.
    # 실패 시 fallback 없이 즉시 에러 발생
    
    if all_blocks:
        has_block_ids = all(block.get("id") for block in all_blocks)
        if not has_block_ids:
            error_msg = "메인에이전트가 Neo4j 블록 ID를 반환하지 않음 - 분석 실패 (아키텍처 B: 메인이 Neo4j 저장 담당)"
            log_nontarget("WORKFLOW", f"[ERROR] {error_msg}")
        if ctx.trace_logger:
                ctx.trace_logger.log_error("workflow", error_msg, {"blocks_count": len(all_blocks)})
            raise RuntimeError(error_msg)
        log_nontarget("WORKFLOW", "메인에이전트가 Neo4j 작업 완료 확인 (아키텍처 B)")
        
        # NONTARGET_BLOCK summary 벡터화 (의미 검색용)
        try:
            vectorized = await vectorize_nontarget_blocks(file_path, ctx)
            if vectorized > 0:
                log_nontarget("WORKFLOW", f"NONTARGET_BLOCK 벡터화: {vectorized}개 완료")
        except Exception as e:
            log_nontarget("WORKFLOW", f"벡터화 실패 (비치명적): {e}")
    
    # 최종 상태 결정
    status = "success" if all_blocks else "partial"
    if not chunks:
        status = "failed"
    
    # 결과 저장
    final_state: NontargetState = {
        "file_path": file_path,
        "blocks": all_blocks,
        "summaries": all_summaries if all_summaries else all_blocks,
        "status": status,
        "error": None,
        "memory_updates": all_memory_updates,
        "corrections_made": all_corrections,
    }
    
    # 트레이스 완료
        if ctx.trace_logger:
        ctx.trace_logger.log_workflow_complete(final_state, None)
        ctx.trace_logger.generate_report()
        log_nontarget("WORKFLOW", f"트레이스 보고서: {ctx.trace_logger.get_trace_dir()}")
        
    return final_state


# ============================================================================
# NONTARGET_BLOCK 벡터화
# ============================================================================

async def vectorize_nontarget_blocks(
    file_path: str,
    ctx: RuntimeCtx,
) -> int:
    """NONTARGET_BLOCK의 summary를 벡터화합니다.
    
    메인에이전트가 Neo4j에 NONTARGET_BLOCK을 저장한 후 호출합니다.
    summary가 있고 summary_vec이 없는 블록을 대상으로 벡터를 생성합니다.
    
    Args:
        file_path: 파일 경로 (해당 파일의 블록만 대상)
        ctx: RuntimeCtx
        
    Returns:
        벡터화된 블록 수
    """
    if not ctx.client:
        return 0
    
    api_key = getattr(settings, 'openai_api_key', None)
    if not api_key:
        log_nontarget("VECTORIZE", "OpenAI API 키 없음 - 벡터화 건너뜀")
        return 0
    
    openai_client = AsyncOpenAI(api_key=api_key)
    embedding_client = EmbeddingClient(openai_client)
    
    # summary가 있고 summary_vec이 없는 NONTARGET_BLOCK 조회
    query = """
    MATCH (n:NONTARGET_BLOCK)
    WHERE n.file_path = $file_path
      AND n.summary IS NOT NULL
      AND n.summary <> ''
      AND (n.summary_vec IS NULL OR size(n.summary_vec) = 0)
    RETURN elementId(n) AS nid, n.summary AS summary
    """
    
    try:
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(query, {"file_path": file_path})
        else:
            result = await ctx.client.execute_with_params(query, {"file_path": file_path})
    except Exception as e:
        log_nontarget("VECTORIZE", f"NONTARGET_BLOCK 조회 실패: {e}")
        return 0
    
    nodes = result if result else []
    if not nodes:
        return 0
    
    # 배치 처리 (50개씩)
    batch_size = 50
    vectorized_count = 0
    
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i:i + batch_size]
        texts = []
        for item in batch:
            summary = item.get("summary", "") if isinstance(item, dict) else item.data().get("summary", "")
            texts.append(summary)
        
        try:
            embed_start = time.time()
            vectors = await embedding_client.embed_batch(texts)
            embed_time = time.time() - embed_start
            
            log_nontarget("VECTORIZE", f"NONTARGET_BLOCK {len(vectors)}개 임베딩 ({embed_time:.2f}초)")
            
            vector_updates = []
            for item, vector in zip(batch, vectors):
                if vector:
                    nid = item.get("nid", "") if isinstance(item, dict) else item.data().get("nid", "")
                    vector_updates.append({"nid": nid, "vector": vector})
                    vectorized_count += 1
            
            if vector_updates:
                update_query = """
                UNWIND $items AS item
                MATCH (n) WHERE elementId(n) = item.nid
                SET n.summary_vec = item.vector
                """
                if ctx.cypher_lock:
                    async with ctx.cypher_lock:
                        await ctx.client.execute_with_params(update_query, {"items": vector_updates})
                else:
                    await ctx.client.execute_with_params(update_query, {"items": vector_updates})
        except Exception as e:
            log_nontarget("VECTORIZE", f"벡터화 실패: {e}")
    
    log_nontarget("VECTORIZE", f"NONTARGET_BLOCK 벡터화 완료: {vectorized_count}개 ({file_path})")
    return vectorized_count


# ============================================================================
# 워크플로우 실행 함수
# ============================================================================

async def run_workflow(
    file_path: str,
    ctx: RuntimeCtx,
    main_agent: Any,
    deepagents_client: DeepAgentsClient,
    thread_id: Optional[str] = None,
    enable_tracing: bool = True,
) -> NontargetState:
    """워크플로우 실행 (자율 에이전트)
    
    에이전트는 외부에서 1회 생성되어 주입됩니다.
    파일별 동적 데이터는 task 메시지(HumanMessage)로 전달됩니다.
    
    Args:
        file_path: 대상 파일 경로
        ctx: 런타임 컨텍스트
        main_agent: 사전 생성된 메인 에이전트 (서브에이전트 포함)
        deepagents_client: task 메시지 렌더링용 클라이언트
        thread_id: (무시됨 - 하위 호환용)
        enable_tracing: 트레이스 로깅 활성화
        
    Returns:
        분석 결과 상태
    """
    return await run_autonomous_agent(
        file_path, ctx, main_agent, deepagents_client, enable_tracing
    )
