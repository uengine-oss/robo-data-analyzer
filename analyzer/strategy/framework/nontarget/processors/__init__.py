"""처리 로직 패키지

청킹, 링킹, 테이블 처리, 수정 로직입니다.
"""

from analyzer.strategy.framework.nontarget.processors.chunk_processor import (
    extract_chunks_for_analysis,
    discover_patterns_iteratively,
    load_rules_from_memory,
    save_rules_to_memory,
)
from analyzer.strategy.framework.nontarget.processors.correction_processor import (
    ACorrection,
    BCorrection,
    CCorrection,
)

__all__ = [
    # chunker
    "extract_chunks_for_analysis",
    "discover_patterns_iteratively",
    "load_rules_from_memory",
    "save_rules_to_memory",
    # correction
    "ACorrection",
    "BCorrection",
    "CCorrection",
]

