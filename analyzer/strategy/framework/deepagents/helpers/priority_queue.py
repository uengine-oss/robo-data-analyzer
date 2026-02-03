"""
Priority Queue (R2: 큐 레벨 dedup 강제)

후보 우선순위 큐.
dedup_key set + max_queue_size 강제.

사용 예시:
    queue = PriorityQueue(max_size=10000)
    
    # 후보 추가
    queue.push(candidate, priority=80, dedup_key="key123")
    
    # 우선순위 높은 순으로 꺼내기
    candidate = queue.pop()
"""

import heapq
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List, Set
from datetime import datetime
import hashlib


@dataclass(order=True)
class PriorityItem:
    """우선순위 큐 항목"""
    priority: int = field(compare=True)       # 높을수록 우선 (음수로 저장)
    timestamp: float = field(compare=True)    # 같은 우선순위면 먼저 들어온 것
    dedup_key: str = field(compare=False)     # 중복 제거 키
    candidate: Any = field(compare=False)     # 실제 후보 데이터
    
    def __post_init__(self):
        # heapq는 최소힙이므로 우선순위를 음수로 저장
        self.priority = -self.priority


@dataclass
class ResolutionCandidate:
    """해석 후보"""
    element_id: str           # S3: elementId 사용
    node_type: str            # AST 노드 타입
    artifact_type: str        # 아티팩트 타입
    
    # 힌트 정보
    external_hints: List[str] = field(default_factory=list)
    search_tokens: List[str] = field(default_factory=list)  # 비즈니스 단어 토큰 (유사도 매칭용)
    missing_bindings: List[str] = field(default_factory=list)
    literals: List[str] = field(default_factory=list)
    callees: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    
    # 코드 정보
    code_compact: str = ""
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    
    # 메타데이터
    confidence: float = 0.5
    priority: int = 50
    cheapness: int = 50
    token: int = 0  # Neo4j token 속성 (청크 구성용)
    
    # 상태
    created_at: datetime = field(default_factory=datetime.now)
    
    def get_dedup_key(self) -> str:
        """중복 제거 키 생성 (R2)"""
        key_parts = [
            self.element_id,
            self.artifact_type,
            self.external_hints[0] if self.external_hints else "",
        ]
        key_str = "|".join(key_parts)
        return hashlib.sha256(key_str.encode()).hexdigest()[:16]
    
    def calculate_priority(self) -> int:
        """우선순위 계산"""
        score = self.priority
        
        # cheapness 반영 (cheap한 것 우선)
        score += self.cheapness // 2
        
        # 힌트가 많으면 우선
        score += len(self.external_hints) * 5
        
        # 높은 confidence 우선
        score += int(self.confidence * 20)
        
        return min(score, 200)  # 최대 200
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "element_id": self.element_id,
            "node_type": self.node_type,
            "artifact_type": self.artifact_type,
            "external_hints": self.external_hints,
            "missing_bindings": self.missing_bindings,
            "literals": self.literals,
            "callees": self.callees,
            "annotations": self.annotations,
            "code_compact": self.code_compact,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "confidence": self.confidence,
            "priority": self.priority,
            "cheapness": self.cheapness,
            "token": self.token,
            "created_at": self.created_at.isoformat(),
            "dedup_key": self.get_dedup_key(),
        }


class PriorityQueue:
    """
    우선순위 큐 (R2: dedup 강제)
    
    특징:
    - dedup_key로 중복 방지
    - max_size 제한
    - 우선순위 높은 순으로 처리
    """
    
    def __init__(
        self, 
        max_size: int = 10000,
        overflow_policy: str = "drop_lowest_priority"
    ):
        """
        Args:
            max_size: 최대 큐 크기
            overflow_policy: 초과 시 정책 (drop_lowest_priority | reject_new)
        """
        self._heap: List[PriorityItem] = []
        self._dedup_set: Set[str] = set()
        self._max_size = max_size
        self._overflow_policy = overflow_policy
        
        # 통계
        self._total_pushed = 0
        self._total_popped = 0
        self._duplicates_rejected = 0
        self._overflow_dropped = 0
    
    def push(
        self, 
        candidate: ResolutionCandidate,
        priority: Optional[int] = None,
        dedup_key: Optional[str] = None
    ) -> bool:
        """
        후보 추가
        
        Args:
            candidate: 해석 후보
            priority: 우선순위 (없으면 자동 계산)
            dedup_key: 중복 제거 키 (없으면 자동 생성)
        
        Returns:
            추가 성공 여부
        """
        # 1. dedup_key 결정
        if dedup_key is None:
            dedup_key = candidate.get_dedup_key()
        
        # 2. 중복 체크 (R2)
        if dedup_key in self._dedup_set:
            self._duplicates_rejected += 1
            return False
        
        # 3. 우선순위 결정
        if priority is None:
            priority = candidate.calculate_priority()
        
        # 4. 큐 크기 체크
        if len(self._heap) >= self._max_size:
            if self._overflow_policy == "reject_new":
                self._overflow_dropped += 1
                return False
            elif self._overflow_policy == "drop_lowest_priority":
                # 가장 낮은 우선순위 제거 (heapq는 최소힙이므로 마지막)
                if self._heap:
                    removed = heapq.heappop(self._heap)
                    self._dedup_set.discard(removed.dedup_key)
                    self._overflow_dropped += 1
        
        # 5. 추가
        item = PriorityItem(
            priority=priority,
            timestamp=datetime.now().timestamp(),
            dedup_key=dedup_key,
            candidate=candidate,
        )
        heapq.heappush(self._heap, item)
        self._dedup_set.add(dedup_key)
        self._total_pushed += 1
        
        return True
    
    def pop(self) -> Optional[ResolutionCandidate]:
        """
        가장 높은 우선순위의 후보 꺼내기
        
        Returns:
            후보 (없으면 None)
        """
        if not self._heap:
            return None
        
        item = heapq.heappop(self._heap)
        self._dedup_set.discard(item.dedup_key)
        self._total_popped += 1
        
        return item.candidate
    
    def peek(self) -> Optional[ResolutionCandidate]:
        """가장 높은 우선순위 후보 확인 (제거하지 않음)"""
        if not self._heap:
            return None
        return self._heap[0].candidate
    
    def remove_by_dedup_key(self, dedup_key: str) -> bool:
        """특정 dedup_key의 항목 제거"""
        if dedup_key not in self._dedup_set:
            return False
        
        # 해당 항목 찾아서 제거
        for i, item in enumerate(self._heap):
            if item.dedup_key == dedup_key:
                self._heap[i] = self._heap[-1]
                self._heap.pop()
                heapq.heapify(self._heap)
                self._dedup_set.discard(dedup_key)
                return True
        
        return False
    
    def contains(self, dedup_key: str) -> bool:
        """dedup_key 존재 여부"""
        return dedup_key in self._dedup_set
    
    def __len__(self) -> int:
        return len(self._heap)
    
    def is_empty(self) -> bool:
        return len(self._heap) == 0
    
    def is_full(self) -> bool:
        return len(self._heap) >= self._max_size
    
    def clear(self):
        """큐 비우기"""
        self._heap.clear()
        self._dedup_set.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 조회"""
        return {
            "current_size": len(self._heap),
            "max_size": self._max_size,
            "total_pushed": self._total_pushed,
            "total_popped": self._total_popped,
            "duplicates_rejected": self._duplicates_rejected,
            "overflow_dropped": self._overflow_dropped,
        }

