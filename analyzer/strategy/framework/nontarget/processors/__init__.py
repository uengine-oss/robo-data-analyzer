"""처리 로직 패키지

블록 분리(block_splitter) 전용 모듈입니다.
레거시 chunk_processor 유틸은 제거되었습니다.
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

__all__ = [
    "BlockJSON",
    "ChunkerContext",
    "ChunkingRule",
    "BoundaryCandidate",
    "OpenBlockState",
    "read_file_with_line_numbers",
    "make_overlap_windows",
    "split_blocks",
]
