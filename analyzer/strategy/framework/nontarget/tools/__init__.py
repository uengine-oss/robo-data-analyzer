"""도구 패키지

4개 독립 DeepAgents가 사용할 읽기 전용 도구들입니다.

도구 분류:
- 그래프 검색 (읽기 전용): Neo4j 조회, 의미 검색, 키워드 검색
- 메모리 관리: 읽기/쓰기/목록
- 컨텍스트 관리: 토큰 확인, 압축
"""

from typing import Any, List

from langchain_core.tools import StructuredTool

from analyzer.strategy.framework.nontarget.tools.neo4j_tool import (
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


def get_analysis_tools(ctx: Any) -> List[StructuredTool]:
    """분석용 읽기 전용 도구 반환 (4개 에이전트 공통)

    모든 에이전트는 Neo4j에 직접 쓰지 않습니다.
    JSON 결과만 반환하고, 시스템이 템플릿 Cypher로 저장합니다.

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
        # 메모리 관리 도구
        create_read_memory_tool(ctx),
        create_write_memory_tool(ctx),
        create_list_memory_tool(),
        # 컨텍스트 관리 도구
        create_check_context_limit_tool(),
        create_compress_context_tool(ctx),
    ]


__all__ = [
    "get_analysis_tools",
    "ensure_vector_indexes",
    "count_tokens_tiktoken",
    "auto_check_context",
]
