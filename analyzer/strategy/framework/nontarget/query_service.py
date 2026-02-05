"""main_agent를 활용한 그래프 질의 응답 서비스

main_agent가 도구를 사용하여 그래프 기반 질의 응답을 수행합니다.
A/B/C 수정용 서브에이전트도 지원합니다.

도구:
- neo4j_cypher: Cypher 쿼리 실행
- semantic_search_summary: 의미 검색
- lex_search_node_code: 키워드 검색

서브에이전트:
- rechunk-agent: 블록 경계 문제(A 수정) 처리
- resummarize-agent: 요약 문제(B 수정) 처리
- relink-agent: 연결 문제(C 수정) 처리
"""

from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from analyzer.strategy.framework.nontarget.nontarget_logger import log_nontarget, log_agent_action
from analyzer.strategy.framework.nontarget.deepagents_client import (
    DeepAgentsClient,
    _extract_agent_output,
)
from analyzer.strategy.framework.nontarget.workflow import RuntimeCtx


# ============================================================================
# 질의 응답 에이전트 생성
# ============================================================================

def create_query_agent(ctx: RuntimeCtx) -> Any:
    """질의 응답용 main_agent 생성
    
    main_agent는 다음 도구를 사용합니다:
    - neo4j_cypher: Cypher 쿼리 실행
    - semantic_search_summary: 의미 검색
    - lex_search_node_code: 키워드 검색
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        DeepAgents 에이전트
    """
    log_agent_action("main_agent", "create_query_agent", {})
    
    client = DeepAgentsClient()
    agent = client.create_agent("main_agent", ctx=ctx)
    
    return agent


async def query_graph(
    question: str,
    ctx: RuntimeCtx,
) -> Dict[str, Any]:
    """그래프 기반 질의 응답
    
    main_agent가 도구를 사용하여 질문에 답변합니다.
    
    Args:
        question: 질문 텍스트
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        답변 딕셔너리 {answer, evidence, uncertainty, follow_up}
    """
    log_agent_action("main_agent", "query_graph", {"question": question[:100]})
    
    agent = create_query_agent(ctx)
    
    try:
        result = agent.invoke({"messages": [HumanMessage(content=question)]})
        output = _extract_agent_output(result)
        
        log_nontarget("QUERY", f"질의 응답 완료: {len(output)} chars")
        
        return {
            "success": True,
            "answer": output,
            "question": question,
        }
        
    except Exception as e:
        log_nontarget("QUERY", f"질의 응답 실패: {e}")
        return {
            "success": False,
            "error": str(e),
            "question": question,
        }


# ============================================================================
# A/B/C 수정 서비스 (correction.py 클래스 호출로 통합)
# ============================================================================

async def request_correction_a(
    file_path: str,
    ctx: RuntimeCtx,
) -> Dict[str, Any]:
    """A 수정 요청: 블록 경계 재분석
    
    correction.py의 ACorrection.correct를 호출합니다.
    
    Args:
        file_path: 대상 파일 경로
        ctx: RuntimeCtx
        
    Returns:
        수정 결과 딕셔너리
    """
    from analyzer.strategy.framework.nontarget.correction import ACorrection
    
    log_agent_action("correction_a", "rechunk", {"file_path": file_path})
    return await ACorrection.correct(file_path, ctx)


async def request_correction_b(
    block_id: str,
    ctx: RuntimeCtx,
) -> Dict[str, Any]:
    """B 수정 요청: Summary 재생성
    
    블록 정보를 조회 후 correction.py의 BCorrection.correct를 호출합니다.
    
    Args:
        block_id: 블록 노드 ID (elementId)
        ctx: RuntimeCtx
        
    Returns:
        수정 결과 딕셔너리
    """
    from analyzer.strategy.framework.nontarget.correction import BCorrection
    
    log_agent_action("correction_b", "resummarize", {"block_id": block_id})
    
    if not ctx.client:
        return {"success": False, "error": "Neo4j 클라이언트 없음"}
    
    try:
        # 블록 정보 조회
        query = """
            MATCH (b:NONTARGET_BLOCK)
            WHERE elementId(b) = $block_id
            RETURN b.node_code AS code, b.file_path AS file_path
        """
        
        if ctx.cypher_lock:
            async with ctx.cypher_lock:
                result = await ctx.client.execute_with_params(query, {"block_id": block_id})
        else:
            result = await ctx.client.execute_with_params(query, {"block_id": block_id})
        
        if not result:
            return {"success": False, "error": "블록을 찾을 수 없음", "block_id": block_id}
        
        record = result[0]
        code = record.get("code", record.get("b.node_code", ""))
        
        # BCorrection.correct 호출
        return await BCorrection.correct(
            block_id=block_id,
            block_code=code,
            file_type="unknown",
            ctx=ctx,
        )
        
    except Exception as e:
        log_nontarget("CORRECTION_B", f"Summary 재생성 실패: {e}")
        return {
            "success": False,
            "error": str(e),
            "block_id": block_id,
        }


async def request_correction_c(
    block_id: str,
    old_link_ids: List[str],
    new_keywords: List[str],
    ctx: RuntimeCtx,
) -> Dict[str, Any]:
    """C 수정 요청: REFERS_TO 관계 재생성
    
    correction.py의 CCorrection.correct를 호출합니다.
    
    Args:
        block_id: 블록 노드 ID
        old_link_ids: 삭제할 기존 링크 ID 리스트
        new_keywords: 새로 연결할 키워드 리스트
        ctx: RuntimeCtx
        
    Returns:
        수정 결과 딕셔너리
    """
    from analyzer.strategy.framework.nontarget.correction import CCorrection
    
    log_agent_action("correction_c", "relink", {"block_id": block_id})
    
    # new_keywords를 new_links 형식으로 변환
    new_links = [{"keyword": kw, "confidence": 0.5} for kw in new_keywords]
    
    return await CCorrection.correct(
        block_id=block_id,
        remove_link_ids=old_link_ids,
        new_links=new_links,
        ctx=ctx,
    )


# ============================================================================
# 간편 인터페이스
# ============================================================================

class QueryService:
    """질의 응답 서비스 클래스
    
    main_agent를 사용하여 그래프 기반 질의 응답을 수행합니다.
    """
    
    def __init__(self, ctx: RuntimeCtx):
        """초기화
        
        Args:
            ctx: RuntimeCtx (client, cypher_lock)
        """
        self.ctx = ctx
        self._agent = None
    
    @property
    def agent(self) -> Any:
        """에이전트 (지연 생성)"""
        if self._agent is None:
            self._agent = create_query_agent(self.ctx)
        return self._agent
    
    async def ask(self, question: str) -> Dict[str, Any]:
        """질문하기
        
        Args:
            question: 질문 텍스트
            
        Returns:
            답변 딕셔너리
        """
        return await query_graph(question, self.ctx)
    
    async def correct_a(self, file_path: str) -> Dict[str, Any]:
        """A 수정: 블록 경계 재분석"""
        return await request_correction_a(file_path, self.ctx)
    
    async def correct_b(self, block_id: str) -> Dict[str, Any]:
        """B 수정: Summary 재생성"""
        return await request_correction_b(block_id, self.ctx)
    
    async def correct_c(
        self,
        block_id: str,
        old_link_ids: List[str],
        new_keywords: List[str],
    ) -> Dict[str, Any]:
        """C 수정: REFERS_TO 관계 재생성"""
        return await request_correction_c(block_id, old_link_ids, new_keywords, self.ctx)

