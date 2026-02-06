"""비타겟 파일 분석 모듈 (Phase 3)

LangGraph + DeepAgents를 활용한 비타겟 파일 분석:

새 구조:
- agents/: 에이전트 클라이언트
- tools/: Neo4j/검색/메모리/컨텍스트 도구
- processors/: 청킹, 링킹, 테이블 처리, 수정 로직
- utils/: 로거, 트레이스, 쿼리 서비스
- workflow.py: 메인 워크플로우
- nontarget_phase.py: Phase 3 진입점
"""

from analyzer.strategy.framework.nontarget.nontarget_phase import run_nontarget_phase
from analyzer.strategy.framework.nontarget.workflow import (
    run_workflow,
    RuntimeCtx,
    NontargetState,
)

# agents
from analyzer.strategy.framework.nontarget.agents.deepagents_client import (
    DeepAgentsClient,
    generate_summary_with_agent,
    save_run_record,
    read_memory_file,
    write_memory_file,
)

# tools
from analyzer.strategy.framework.nontarget.tools import (
    get_all_tools,
    get_tools_by_names,
)
from analyzer.strategy.framework.nontarget.tools.neo4j_tool import create_neo4j_cypher_tool
from analyzer.strategy.framework.nontarget.tools.search_tool import (
    create_semantic_search_tool,
    create_lex_search_tool,
)

# processors
from analyzer.strategy.framework.nontarget.processors.chunk_processor import (
    discover_patterns_iteratively,
    extract_samples_iterative,
)
from analyzer.strategy.framework.nontarget.processors.correction_processor import (
    ACorrection,
    BCorrection,
    CCorrection,
    run_correction,
    get_modification_history,
)

# utils
from analyzer.strategy.framework.nontarget.utils.query_service import (
    QueryService,
    query_graph,
    create_query_agent,
    request_correction_a,
    request_correction_b,
    request_correction_c,
)
from analyzer.strategy.framework.nontarget.utils.trace_logger import TraceLogger

__all__ = [
    # Phase 실행
    "run_nontarget_phase",
    # 워크플로우
    "run_workflow",
    "RuntimeCtx",
    "NontargetState",
    # DeepAgents
    "DeepAgentsClient",
    "generate_summary_with_agent",
    # 도구
    "get_all_tools",
    "get_tools_by_names",
    "create_neo4j_cypher_tool",
    "create_semantic_search_tool",
    "create_lex_search_tool",
    # 청크 (반복 샘플링)
    "discover_patterns_iteratively",
    "extract_samples_iterative",
    # 질의 응답 서비스
    "QueryService",
    "query_graph",
    "create_query_agent",
    "request_correction_a",
    "request_correction_b",
    "request_correction_c",
    # 메모리
    "save_run_record",
    "read_memory_file",
    "write_memory_file",
    # 수정
    "ACorrection",
    "BCorrection",
    "CCorrection",
    "run_correction",
    "get_modification_history",
    # 트레이싱
    "TraceLogger",
]
