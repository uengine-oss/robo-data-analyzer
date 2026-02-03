"""
Runtime Policy - 가드레일 대체

최우선 규칙:
1. DDL 없어도 무조건 생성
2. 테이블 발견되면 FROM/WRITES 무조건 생성
3. confidence는 우선순위용이지 커밋 조건이 아님

도구 제한:
- MAX_SLICE_LIMIT: 30KB
- MAX_TOOL_CALLS: 10 (초과 시 도구 제한, 커밋은 진행)

Review reason (제한적 사용!):
- tool_limit: tool 호출 >= 10
- write_conflict: write의 primary table 2개 이상
"""

from typing import Tuple, Optional
from dataclasses import dataclass


@dataclass
class PolicyLimits:
    """정책 제한값"""
    max_slice_limit: int = 30000  # 30KB (바이트)
    max_tool_calls: int = 10
    max_search_fails: int = 2
    max_retries: int = 2
    min_confidence_for_write: float = 0.8


# 기본 정책 인스턴스
DEFAULT_LIMITS = PolicyLimits()


class RuntimePolicy:
    """
    런타임 정책 (가드레일 대체)
    
    코드로 강제되는 제한:
    - docs_slice limit 강제 (30KB)
    - tool 호출 횟수 상한 (10회)
    - 동일 키 재검색 금지 (캐시 강제)
    - 커밋 전 최소 증거 체크
    """
    
    # 클래스 레벨 상수 (하위 호환성)
    MAX_SLICE_LIMIT = DEFAULT_LIMITS.max_slice_limit
    MAX_TOOL_CALLS = DEFAULT_LIMITS.max_tool_calls
    MAX_SEARCH_FAILS = DEFAULT_LIMITS.max_search_fails
    MAX_RETRIES = DEFAULT_LIMITS.max_retries
    
    def __init__(self, limits: Optional[PolicyLimits] = None):
        """
        Args:
            limits: 커스텀 제한값 (기본값: DEFAULT_LIMITS)
        """
        self.limits = limits or DEFAULT_LIMITS
    
    def validate_slice_limit(self, limit: int) -> int:
        """
        슬라이스 limit 검증 및 강제
        
        Args:
            limit: 요청된 limit
        
        Returns:
            강제된 limit (최대 30KB)
        """
        return min(limit, self.limits.max_slice_limit)
    
    def should_review(self, evidence: "EvidenceData") -> Tuple[bool, str]:
        """
        리뷰 전이 조건 검사
        
        플랜 규칙 (우선순위):
        1. tool 호출 >= 10 → review(tool_limit)
        2. docs_search 실패 >= 2 → review(search_fail)
        3. 재시도 >= 2 && 미확정 → review(retry_exhausted)
        
        Args:
            evidence: EvidenceData 인스턴스
        
        Returns:
            (should_review: bool, reason_code: str)
        """
        # 우선순위 1: tool 호출 상한
        if evidence.tool_call_count >= self.limits.max_tool_calls:
            return True, "tool_limit"
        
        # 우선순위 2: 검색 실패 상한
        if evidence.search_fail_count >= self.limits.max_search_fails:
            return True, "search_fail"
        
        # 우선순위 3: 재시도 상한 (working 상태일 때만)
        if evidence.attempts >= self.limits.max_retries and evidence.status == "working":
            return True, "retry_exhausted"
        
        return False, ""
    
    def should_apply_writes(self, confidence: float) -> bool:
        """
        writes 관계 적용 여부 결정
        
        🚨 새 정책:
        - confidence는 우선순위용이지 커밋 조건이 아님
        - operation이 write면 WRITES, 아니면 FROM
        
        이 함수는 하위 호환성을 위해 유지하지만,
        실제로는 operation 자체로 판단해야 함
        
        Args:
            confidence: 신뢰도 (참고용, 커밋 차단 조건 아님)
        
        Returns:
            항상 True (confidence로 차단 안 함)
        """
        # 🚨 confidence로 writes 차단하지 않음
        return True
    
    def can_commit(self, evidence: "EvidenceData") -> Tuple[bool, str]:
        """
        커밋 가능 여부 검사
        
        🚨 새 정책:
        - 테이블 발견되면 무조건 커밋
        - confidence < 0.7 → 커밋 차단 아님!
        - DDL 없음 → 커밋 차단 아님!
        
        Args:
            evidence: EvidenceData 인스턴스
        
        Returns:
            (can_commit: bool, reason: str)
        """
        # 테이블 없음만 유일한 실패 조건
        if not evidence.tables:
            return False, "no_tables"
        
        # 🚨 confidence 체크 제거: 테이블 있으면 무조건 커밋
        return True, ""
    
    @staticmethod
    def get_review_reason_description(reason: str) -> str:
        """
        리뷰 reason 코드 설명
        
        Args:
            reason: reason 코드
        
        Returns:
            설명 문자열
        """
        descriptions = {
            "tool_limit": "도구 호출 횟수 상한 초과 (10회)",
            "search_fail": "문서 검색 실패 횟수 상한 초과 (2회)",
            "write_conflict": "쓰기 대상 테이블 충돌 (primary table 2개 이상)",
            "table_mismatch": "entity XML과 SQL 결과 테이블 불일치",
            "retry_exhausted": "재시도 횟수 상한 초과 (2회)",
            "no_tables": "확정된 테이블 없음",
            "low_confidence": "평균 신뢰도 부족 (< 0.7)",
        }
        return descriptions.get(reason, f"알 수 없는 이유: {reason}")


# 전역 정책 인스턴스 (편의용)
POLICY = RuntimePolicy()


def validate_slice_limit(limit: int) -> int:
    """전역 정책으로 슬라이스 limit 검증"""
    return POLICY.validate_slice_limit(limit)


def should_review(evidence: "EvidenceData") -> Tuple[bool, str]:
    """전역 정책으로 리뷰 조건 검사"""
    return POLICY.should_review(evidence)


def should_apply_writes(confidence: float) -> bool:
    """전역 정책으로 writes 적용 여부 결정"""
    return POLICY.should_apply_writes(confidence)

