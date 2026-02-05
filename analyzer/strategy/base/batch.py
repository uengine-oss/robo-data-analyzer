"""공통 배치 처리 모듈

전략 간 공유하는 배치 계획 및 실행 로직.

주요 구성:
- AnalysisBatch: 분석 배치 정보
- BatchPlanner: 토큰 한도 기반 배치 분할
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from config.settings import settings
from util.text_utils import log_process

if TYPE_CHECKING:
    from analyzer.strategy.base.statement_node import StatementNode

# 설정에서 가져오는 상수
MAX_BATCH_TOKEN = settings.batch.max_batch_token
MAX_CONTEXT_TOKEN = settings.batch.max_context_token


@dataclass(slots=True)
class AnalysisBatch:
    """분석 배치 정보.
    
    공통 필드:
    - batch_id: 배치 ID
    - nodes: 배치에 포함된 노드 리스트
    - ranges: 분석 범위 리스트
    - progress_line: 진행률 표시용 라인 번호
    
    DBMS 확장 필드:
    - dml_ranges: DML 범위 리스트 (DBMS 전용)
    """
    batch_id: int
    nodes: List["StatementNode"]
    ranges: List[Dict[str, int]]
    progress_line: int
    
    # DBMS 전용 (선택적)
    dml_ranges: List[Dict[str, int]] = field(default_factory=list)

    def build_payload(self) -> Tuple[str, str]:
        """LLM 호출용 코드와 컨텍스트를 분리하여 반환합니다.
        
        Returns:
            (code, context) 튜플 - 코드와 컨텍스트를 분리
        """
        code_parts: List[str] = []
        context_parts: List[str] = []
        
        for node in self.nodes:
            # 원본과 동일하게 항상 get_compact_code() 호출
            code = node.get_compact_code()
            code_parts.append(code)
            
            context = node.get_ancestor_context(MAX_CONTEXT_TOKEN)
            if context:
                context_parts.append(context)
            else:
                context_parts.append("")
        
        return '\n\n'.join(code_parts), '\n\n'.join(context_parts)

    def build_dml_payload(self) -> Optional[Tuple[str, str]]:
        """DML 노드만 추린 코드와 컨텍스트를 분리하여 반환합니다.
        
        DBMS 전용 메서드.
        
        Returns:
            (code, context) 튜플 또는 None - 코드와 컨텍스트를 분리
        """
        dml_nodes = [node for node in self.nodes if getattr(node, 'dml', False)]
        if not dml_nodes:
            return None
        
        code_parts: List[str] = []
        context_parts: List[str] = []
        
        for node in dml_nodes:
            code = node.get_compact_code() if node.has_children else node.get_raw_code()
            code_parts.append(code)
            
            context = node.get_ancestor_context(MAX_CONTEXT_TOKEN)
            if context:
                context_parts.append(context)
            else:
                context_parts.append("")
        
        return '\n\n'.join(code_parts), '\n\n'.join(context_parts)


class BatchPlanner:
    """수집된 노드를 토큰 한도 내에서 배치로 묶습니다."""
    
    def __init__(self, token_limit: int = MAX_BATCH_TOKEN):
        """토큰 한도를 지정하여 배치 생성기를 초기화합니다."""
        self.token_limit = token_limit

    def plan(
        self, 
        nodes: List["StatementNode"], 
        include_dml_ranges: bool = False
    ) -> List[AnalysisBatch]:
        """토큰 한도를 넘지 않도록 노드를 분할하여 분석 배치를 생성합니다.
        
        Args:
            nodes: 분석 대상 노드 리스트
            include_dml_ranges: True이면 dml_ranges 계산 (DBMS용)
            
        Returns:
            배치 리스트
        """
        batches: List[AnalysisBatch] = []
        current_nodes: List["StatementNode"] = []
        current_tokens = 0
        batch_id = 1

        for node in nodes:
            if not node.analyzable:
                continue

            # 부모 노드는 자식 요약이 준비된 후 단독으로 실행되므로 즉시 배치를 확정합니다.
            if node.has_children:
                # 현재까지 누적된 리프 배치를 먼저 확정합니다.
                if current_nodes:
                    log_process("ANALYZE", "BATCH", f"📦 배치 #{batch_id} 확정: 리프 노드 {len(current_nodes)}개 (토큰 {current_tokens}/{self.token_limit})")
                    batches.append(self._create_batch(batch_id, current_nodes, include_dml_ranges))
                    batch_id += 1
                    current_nodes = []
                    current_tokens = 0

                log_process("ANALYZE", "BATCH", f"📦 배치 #{batch_id} 확정: 부모 노드 단독 실행 (라인 {node.start_line}~{node.end_line}, 토큰 {node.token})")
                batches.append(self._create_batch(batch_id, [node], include_dml_ranges))
                batch_id += 1
                continue

            # 현재 배치가 토큰 한도를 초과한다면 쌓인 리프 노드들을 먼저 실행합니다.
            if current_nodes and current_tokens + node.token > self.token_limit:
                log_process("ANALYZE", "BATCH", f"📦 배치 #{batch_id} 확정: 토큰 한도 도달로 선 실행 (누적 {current_tokens}/{self.token_limit})")
                batches.append(self._create_batch(batch_id, current_nodes, include_dml_ranges))
                batch_id += 1
                current_nodes = []
                current_tokens = 0

            current_nodes.append(node)
            current_tokens += node.token

        if current_nodes:
            log_process("ANALYZE", "BATCH", f"📦 배치 #{batch_id} 확정: 마지막 리프 노드 {len(current_nodes)}개 (토큰 {current_tokens}/{self.token_limit})")
            batches.append(self._create_batch(batch_id, current_nodes, include_dml_ranges))

        return batches

    def _create_batch(
        self, 
        batch_id: int, 
        nodes: List["StatementNode"],
        include_dml_ranges: bool = False
    ) -> AnalysisBatch:
        """배치 ID와 노드 리스트로 AnalysisBatch 객체를 생성합니다."""
        ranges = [{"startLine": node.start_line, "endLine": node.end_line} for node in nodes]
        progress = max(node.end_line for node in nodes)
        
        # DML 범위 계산 (DBMS용)
        dml_ranges = []
        if include_dml_ranges:
            dml_ranges = [
                {"startLine": node.start_line, "endLine": node.end_line, "type": node.node_type}
                for node in nodes
                if getattr(node, 'dml', False)
            ]
        
        return AnalysisBatch(
            batch_id=batch_id,
            nodes=nodes,
            ranges=ranges,
            progress_line=progress,
            dml_ranges=dml_ranges,
        )

