"""비타겟 파일 분석 모듈 (Phase 3)

4-Step 고정 파이프라인:

Step 1: 청킹 2-Pass (ChunkerAgent — DeepAgent)
Step 2: 요약 (RuleLoader LLM chain, tiktoken 배치)
Step 3: 링크 (확정적 Cypher + LLM chain 파이프라인)
Step 4: Description (RuleLoader LLM chain)

Step 1만 DeepAgent 사용, Step 2/3/4는 고정 파이프라인.
"""

from analyzer.strategy.framework.nontarget.nontarget_phase import run_nontarget_phase
from analyzer.strategy.framework.nontarget.workflow import (
    RuntimeCtx,
    run_chunker_2pass,
    run_summarizer_batched,
    run_linker,
    run_description,
    build_file_summaries,
    build_table_column_evidence,
)

# DeepAgents 클라이언트 (Chunker 전용)
from analyzer.strategy.framework.nontarget.agents.deepagents_client import (
    DeepAgentsClient,
    extract_agent_output,
    parse_json_from_output,
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
from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget

__all__ = [
    # Phase 실행
    "run_nontarget_phase",
    # 워크플로우
    "RuntimeCtx",
    "run_chunker_2pass",
    "run_summarizer_batched",
    "run_linker",
    "run_description",
    "build_file_summaries",
    "build_table_column_evidence",
    # DeepAgents (Chunker 전용)
    "DeepAgentsClient",
    "extract_agent_output",
    "parse_json_from_output",
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
    # 유틸리티
    "log_nontarget",
]
