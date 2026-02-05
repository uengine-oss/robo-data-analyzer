"""비타겟 파일 분석 Phase (Phase 3)

Framework 분석 파이프라인의 Phase 3을 담당합니다.
비타겟 파일(설정, 리소스 등)을 분석하여 그래프에 통합합니다.

비타겟 파일: target 언어 확장자가 아닌 모든 텍스트 파일
- XML, YAML, properties, groovy, SQL 등
"""

import os
from typing import Any, AsyncGenerator, List

from analyzer.strategy.base_analyzer import AnalysisStats
from client.neo4j_client import Neo4jClient
from analyzer.strategy.framework.nontarget.workflow import run_workflow, RuntimeCtx
from analyzer.strategy.framework.nontarget.nontarget_logger import log_nontarget
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
    
    # 각 파일 처리
    for i, file_path in enumerate(nontarget_files, 1):
        # 상대 경로로 표시
        rel_path = os.path.relpath(file_path, nontarget_dir)
        
        log_nontarget("PHASE", f"[{i}/{len(nontarget_files)}] 처리 중: {rel_path}")
        yield emit_message(f"   ├─ [{i}/{len(nontarget_files)}] {rel_path}")
        
        try:
            # 워크플로우 실행 (RuntimeCtx 전달)
            final_state = await run_workflow(
                file_path=file_path,
                ctx=ctx,
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
