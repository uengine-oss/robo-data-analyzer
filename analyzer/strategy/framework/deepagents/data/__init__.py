"""
데이터 관리 모듈

데이터 관리 관련 모듈:
- evidence_manager: 증거 파일 관리 (IO, dedup, merge)
- candidate_enqueuer: 후보 수집 및 큐 관리
"""

from .evidence_manager import (
    EvidenceManager,
    EvidenceData,
    EvidenceRequiredError,
    require_evidence,
    create_evidence_tool,
)

from .candidate_enqueuer import CandidateEnqueuer

__all__ = [
    "EvidenceManager",
    "EvidenceData",
    "EvidenceRequiredError",
    "require_evidence",
    "create_evidence_tool",
    "CandidateEnqueuer",
]

