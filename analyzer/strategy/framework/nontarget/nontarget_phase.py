"""비타겟 파일 분석 Phase (Phase 3)

Framework 분석 파이프라인의 Phase 3을 담당합니다.
비타겟 파일(설정, 리소스 등)을 분석하여 그래프에 통합합니다.

비타겟 파일: target 언어 확장자가 아닌 모든 텍스트 파일
- XML, YAML, properties, groovy, SQL 등

[변경됨] 에이전트 1회 생성 → 모든 파일에 재사용
- Main Agent (서브에이전트 포함)를 Phase 시작 시 1회 생성
- 각 파일은 동일한 에이전트에 task 메시지만 변경하여 처리
- 에이전트 재생성 오버헤드 제거로 성능 대폭 개선
"""

import os
from typing import Any, AsyncGenerator, List

from analyzer.strategy.base_analyzer import AnalysisStats
from client.neo4j_client import Neo4jClient
from analyzer.strategy.framework.nontarget.workflow import run_workflow, RuntimeCtx
from analyzer.strategy.framework.nontarget.agents.deepagents_client import DeepAgentsClient
from analyzer.strategy.framework.nontarget.tools.search_tool import ensure_vector_indexes
from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget
from util.stream_event import emit_message


def _discover_nontarget_files(nontarget_dir: str) -> List[str]:
    """analysis/nontarget 디렉토리에서 비타겟 파일 탐색
    
    nontarget 디렉토리에는 이미 비타겟 파일만 복사되어 있으므로
    모든 파일을 대상으로 합니다.
    
    Args:
        nontarget_dir: analysis/nontarget 디렉토리 경로
        
    Returns:
        비타겟 파일 경로 리스트
    """
    nontarget_files: List[str] = []
    
    for root, dirs, files in os.walk(nontarget_dir):
        for f in files:
            file_path = os.path.join(root, f)
            nontarget_files.append(file_path)
    
    return sorted(nontarget_files)


async def run_nontarget_phase(
    analyzer: Any,
    client: Neo4jClient,
    orchestrator: Any,
    stats: AnalysisStats,
) -> AsyncGenerator[bytes, None]:
    """Phase 3: 비타겟 파일 분석
    
    analysis/nontarget 디렉토리에서 비타겟 파일(설정, 리소스 등)을 찾아 분석합니다.
    
    ⚠️ 에이전트 1회 생성:
    - Main Agent (서브에이전트 포함)를 Phase 시작 시 1회만 생성
    - 모든 파일은 동일한 에이전트에 task 메시지만 변경하여 처리
    - 시스템 프롬프트는 정적, 파일별 동적 데이터는 HumanMessage로 전달
    
    Args:
        analyzer: FrameworkAnalyzer 인스턴스
        client: Neo4j 클라이언트
        orchestrator: 오케스트레이터
        stats: 분석 통계
        
    Yields:
        스트리밍 이벤트
    """
    log_nontarget("PHASE", "Phase 3: 비타겟 파일 분석 시작")
    yield emit_message("   🔍 비타겟 파일 탐색 중...")
    
    # analysis/nontarget 디렉토리 확인
    analysis_dir = orchestrator.dirs.get("analysis", "")
    if not analysis_dir:
        log_nontarget("PHASE", "analysis 디렉토리 설정 없음")
        yield emit_message("   ℹ️ analysis 디렉토리가 설정되지 않았습니다")
        return
    
    nontarget_dir = os.path.join(analysis_dir, "nontarget")
    if not os.path.isdir(nontarget_dir):
        log_nontarget("PHASE", f"nontarget 디렉토리 없음: {nontarget_dir}")
        yield emit_message("   ℹ️ nontarget 디렉토리가 없습니다")
        return
    
    log_nontarget("PHASE", f"nontarget 디렉토리: {nontarget_dir}")
    
    # 비타겟 파일 탐색 (analysis/nontarget 폴더의 모든 파일)
    nontarget_files = _discover_nontarget_files(nontarget_dir)
    
    if not nontarget_files:
        log_nontarget("PHASE", "비타겟 파일 없음")
        yield emit_message("   ℹ️ 분석할 비타겟 파일이 없습니다")
        return
    
    yield emit_message(f"   📋 비타겟 파일 {len(nontarget_files)}개 발견")
    
    # RuntimeCtx 생성 (client, cypher_lock 담기)
    ctx = RuntimeCtx(
        client=client,
        cypher_lock=analyzer._cypher_lock,
    )
    
    # ==================================================================
    # ⚠️ 벡터 인덱스 확인/생성 (의미 검색에 필요)
    # ==================================================================
    try:
        created_indexes = await ensure_vector_indexes(ctx)
        if created_indexes:
            yield emit_message(f"   🗂️ 벡터 인덱스 {len(created_indexes)}개 생성: {created_indexes}")
    except Exception as e:
        log_nontarget("PHASE", f"벡터 인덱스 확인 실패 (비치명적): {e}")
    
    # ==================================================================
    # ⚠️ 에이전트 1회 생성 (모든 파일에 재사용)
    # 시스템 프롬프트는 정적이므로 inputs 불필요
    # 파일별 동적 데이터는 task 메시지(HumanMessage)로 전달
    # ==================================================================
    deepagents_client = DeepAgentsClient()
    
    try:
        main_agent = deepagents_client.create_main_agent_with_subagents(ctx)
        log_nontarget("PHASE", "메인 에이전트 1회 생성 완료 (모든 파일에 재사용)")
        yield emit_message("   🤖 메인 에이전트 생성 완료 (1회 생성, 모든 파일에 재사용)")
    except Exception as e:
        log_nontarget("PHASE", f"에이전트 생성 실패: {e}")
        yield emit_message(f"   ❌ 에이전트 생성 실패: {str(e)[:80]}")
        return
    
    # 각 파일 처리 (동일 에이전트 재사용)
    for i, file_path in enumerate(nontarget_files, 1):
        # 상대 경로로 표시
        rel_path = os.path.relpath(file_path, nontarget_dir)
        
        log_nontarget("PHASE", f"[{i}/{len(nontarget_files)}] 처리 중: {rel_path}")
        yield emit_message(f"   ├─ [{i}/{len(nontarget_files)}] {rel_path}")
        
        try:
            # 워크플로우 실행 (사전 생성된 에이전트 전달)
            final_state = await run_workflow(
                file_path=file_path,
                ctx=ctx,
                main_agent=main_agent,
                deepagents_client=deepagents_client,
            )
            
            blocks_count = len(final_state.get("blocks", []))
            
            if final_state.get("error"):
                yield emit_message(f"   │     ⚠️ 오류: {final_state['error'][:50]}")
            else:
                yield emit_message(f"   │     ✓ {blocks_count}개 블록 생성")
                
        except Exception as e:
            log_nontarget("PHASE", f"처리 실패: {rel_path} - {e}")
            yield emit_message(f"   │     ❌ 처리 실패: {str(e)[:50]}")
    
    log_nontarget("PHASE", f"Phase 3 완료: {len(nontarget_files)}개 파일 처리")
    yield emit_message(f"   ✅ 비타겟 파일 분석 완료: {len(nontarget_files)}개 파일")
