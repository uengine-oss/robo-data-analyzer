"""처리 로직 패키지

블록 분리(block_splitter) 및 레거시 청킹 유틸입니다.
correction_processor는 삭제됨 — 시스템 품질 게이트로 대체.
"""

from analyzer.strategy.framework.nontarget.processors.block_splitter import (
    BlockJSON,
    ChunkerContext,
    ChunkingRule,
    BoundaryCandidate,
    OpenBlockState,
    read_file_with_line_numbers,
    make_overlap_windows,
    split_blocks,
)

# 레거시 하위호환 (chunk_processor 유틸)
from analyzer.strategy.framework.nontarget.processors.chunk_processor import (
    extract_chunks_for_analysis,
    chunk_by_lines,
    chunk_by_rules,
    extract_sample,
    load_rules_from_memory,
    save_rules_to_memory,
)

__all__ = [
    # block_splitter (새 파이프라인)
    "BlockJSON",
    "ChunkerContext",
    "ChunkingRule",
    "BoundaryCandidate",
    "OpenBlockState",
    "read_file_with_line_numbers",
    "make_overlap_windows",
    "split_blocks",
    # chunk_processor (레거시 하위호환)
    "extract_chunks_for_analysis",
    "chunk_by_lines",
    "chunk_by_rules",
    "extract_sample",
    "load_rules_from_memory",
    "save_rules_to_memory",
]
