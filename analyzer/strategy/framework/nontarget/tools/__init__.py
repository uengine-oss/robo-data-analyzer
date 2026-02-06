"""도구 패키지

DeepAgents 에이전트가 사용할 커스텀 도구들입니다.

도구 분류:
- 읽기 전용 (서브에이전트): Neo4j 조회, 의미 검색, 키워드 검색, 메모리 읽기/쓰기
- 전체 권한 (메인에이전트): 위 모든 도구 + Neo4j 쓰기(MERGE/SET) + 수정(A/B/C)
"""

from typing import Any, List

from langchain_core.tools import StructuredTool

from analyzer.strategy.framework.nontarget.tools.neo4j_tool import (
    create_neo4j_cypher_tool,
    create_neo4j_cypher_tool_readonly,
)
from analyzer.strategy.framework.nontarget.tools.search_tool import (
    create_semantic_search_tool,
    create_lex_search_tool,
    ensure_vector_indexes,
)
from analyzer.strategy.framework.nontarget.tools.memory_tool import (
    create_read_memory_tool,
    create_write_memory_tool,
    create_list_memory_tool,
)
from analyzer.strategy.framework.nontarget.tools.context_tool import (
    create_check_context_limit_tool,
    create_compress_context_tool,
    count_tokens_tiktoken,
    auto_check_context,
)
from analyzer.strategy.framework.nontarget.tools.correction_tool import (
    create_correction_a_tool,
    create_correction_b_tool,
    create_correction_c_tool,
)


def get_readonly_tools(ctx: Any) -> List[StructuredTool]:
    """읽기 전용 도구 반환 (서브에이전트용)
    
    서브에이전트는 Neo4j에 직접 쓰기를 할 수 없습니다.
    JSON 패치 제안만 반환하고, 메인에이전트가 최종 저장을 담당합니다.
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock, trace_logger)
        
    Returns:
        읽기 전용 도구 리스트
    """
    return [
        # 그래프 검색 도구 (읽기 전용)
        create_neo4j_cypher_tool_readonly(ctx),  # MATCH/RETURN만 허용
        create_semantic_search_tool(ctx),
        create_lex_search_tool(ctx),
        # 메모리 관리 도구 (읽기/쓰기 모두 허용)
        create_read_memory_tool(ctx),
        create_write_memory_tool(ctx),
        create_list_memory_tool(),
        # 컨텍스트 관리 도구
        create_check_context_limit_tool(),
        create_compress_context_tool(ctx),
    ]


def get_all_tools(ctx: Any) -> List[StructuredTool]:
    """모든 커스텀 도구 반환 (메인에이전트용)
    
    메인에이전트는 모든 도구를 사용할 수 있습니다.
    Neo4j 쓰기(CREATE, MERGE, SET)와 수정(A/B/C) 도구도 포함됩니다.
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock, trace_logger)
        
    Returns:
        도구 리스트 (쓰기 + 수정 포함)
    """
    return [
        # 그래프 검색/수정 도구 (전체 권한)
        create_neo4j_cypher_tool(ctx),  # CREATE/MERGE/SET 포함
        create_semantic_search_tool(ctx),
        create_lex_search_tool(ctx),
        # 메모리 관리 도구
        create_read_memory_tool(ctx),
        create_write_memory_tool(ctx),
        create_list_memory_tool(),
        # 컨텍스트 관리 도구 (토큰 기준)
        create_check_context_limit_tool(),
        create_compress_context_tool(ctx),
        # 수정(A/B/C) 도구 (메인에이전트 전용)
        create_correction_a_tool(ctx),
        create_correction_b_tool(ctx),
        create_correction_c_tool(ctx),
    ]


def get_tools_by_names(ctx: Any, tool_names: List[str]) -> List[StructuredTool]:
    """이름으로 도구 필터링 (하위 호환용)
    
    Args:
        ctx: RuntimeCtx
        tool_names: 필요한 도구 이름 리스트
        
    Returns:
        필터링된 도구 리스트
    """
    all_tools = get_all_tools(ctx)
    return [t for t in all_tools if t.name in tool_names]


__all__ = [
    "get_all_tools",
    "get_readonly_tools",
    "get_tools_by_names",
    "ensure_vector_indexes",
    "count_tokens_tiktoken",
    "auto_check_context",
]
