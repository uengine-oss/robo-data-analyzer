"""비타겟 파일 분석 모듈 (Phase 3)

4-Step 고정 파이프라인 + 시스템 오케스트레이션:

Step 1: 청킹 2-Pass (ChunkerAgent + block_splitter)
Step 2: 요약 (SummarizerAgent, tiktoken 배치)
Step 3: 링크 (LinkerAgent, kind별 전략)
Step 4: Description (DescriptionAgent, 엔티티 단위)

에이전트 = 분석만 (JSON 반환), 시스템 = 저장만 (템플릿 Cypher).
"""

from analyzer.strategy.framework.nontarget.nontarget_phase import run_nontarget_phase
from analyzer.strategy.framework.nontarget.workflow import (
    RuntimeCtx,
    NontargetState,
    run_chunker_2pass,
    run_summarizer_batched,
    run_linker,
    run_description,
    build_file_summaries,
    build_entity_evidence,
    vectorize_all_blocks,
)

# DeepAgents 클라이언트
from analyzer.strategy.framework.nontarget.agents.deepagents_client import (
    DeepAgentsClient,
    extract_agent_output,
    parse_json_from_output,
)

# 도구
from analyzer.strategy.framework.nontarget.tools import (
    get_analysis_tools,
    ensure_vector_indexes,
)

# Cypher 템플릿
from analyzer.strategy.framework.nontarget.cypher_templates import (
    commit_blocks,
    commit_summaries,
    commit_tables,
    commit_columns,
    commit_links,
    commit_descriptions,
)

# 품질 게이트
from analyzer.strategy.framework.nontarget.quality_gate import (
    quality_gate_phase1_blocks,
    quality_gate_phase1_summaries,
    quality_gate_phase2_links,
)

# 프로세서
from analyzer.strategy.framework.nontarget.processors.block_splitter import (
    BlockJSON,
    ChunkerContext,
    read_file_with_line_numbers,
    make_overlap_windows,
    split_blocks,
)

# 유틸리티
from analyzer.strategy.framework.nontarget.utils.trace_logger import TraceLogger

__all__ = [
    # Phase 실행
    "run_nontarget_phase",
    # 워크플로우
    "RuntimeCtx",
    "NontargetState",
    "run_chunker_2pass",
    "run_summarizer_batched",
    "run_linker",
    "run_description",
    "build_file_summaries",
    "build_entity_evidence",
    "vectorize_all_blocks",
    # DeepAgents
    "DeepAgentsClient",
    "extract_agent_output",
    "parse_json_from_output",
    # 도구
    "get_analysis_tools",
    "ensure_vector_indexes",
    # Cypher 템플릿
    "commit_blocks",
    "commit_summaries",
    "commit_tables",
    "commit_columns",
    "commit_links",
    "commit_descriptions",
    # 품질 게이트
    "quality_gate_phase1_blocks",
    "quality_gate_phase1_summaries",
    "quality_gate_phase2_links",
    # 프로세서
    "BlockJSON",
    "ChunkerContext",
    "read_file_with_line_numbers",
    "make_overlap_windows",
    "split_blocks",
    # 트레이싱
    "TraceLogger",
]
