"""검색 도구

의미 검색(Semantic Search)과 키워드 검색(Lexical Search) 도구입니다.
벡터 인덱스 생성/확인 유틸리티도 포함합니다.
"""

import json
import time
from typing import Annotated, Any, List, Optional

from langchain_core.tools import StructuredTool
from openai import AsyncOpenAI

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget
from client.embedding_client import EmbeddingClient
from config.settings import settings


# ============================================================================
# 벡터 인덱스 유틸리티
# ============================================================================

# 벡터 인덱스 정의: (인덱스명, 노드 레이블, 속성명, 차원, 유사도 함수)
# summary_vec을 가진 모든 노드 레이블에 대해 벡터 인덱스를 생성합니다.
# Phase 2 벡터화 대상: METHOD, CONSTRUCTOR, FIELD, VARIABLE, METHOD_CALL, NEW_INSTANCE, ASSIGNMENT
# Phase 3 벡터화 대상: NONTARGET_BLOCK
VECTOR_INDEX_DEFINITIONS = [
    ("method_vec_idx", "METHOD", "summary_vec", 1536, "cosine"),
    ("constructor_vec_idx", "CONSTRUCTOR", "summary_vec", 1536, "cosine"),
    ("field_vec_idx", "FIELD", "summary_vec", 1536, "cosine"),
    ("variable_vec_idx", "VARIABLE", "summary_vec", 1536, "cosine"),
    ("method_call_vec_idx", "METHOD_CALL", "summary_vec", 1536, "cosine"),
    ("new_instance_vec_idx", "NEW_INSTANCE", "summary_vec", 1536, "cosine"),
    ("assignment_vec_idx", "ASSIGNMENT", "summary_vec", 1536, "cosine"),
    ("nontarget_block_vec_idx", "NONTARGET_BLOCK", "summary_vec", 1536, "cosine"),
]


async def ensure_vector_indexes(ctx: Any) -> List[str]:
    """필요한 Neo4j 벡터 인덱스가 모두 존재하는지 확인하고, 없으면 생성합니다.
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        생성된 인덱스 이름 리스트
    """
    if not ctx or not ctx.client:
        log_nontarget("VECTOR_INDEX", "Neo4j 클라이언트 없음 - 인덱스 생성 건너뜀")
        return []
    
    created = []
    
    # 기존 인덱스 목록 조회
    try:
        existing_indexes_result = await ctx.client.execute_with_params(
            "SHOW INDEXES YIELD name RETURN name", {}
        )
        existing_names = set()
        if existing_indexes_result:
            for r in existing_indexes_result:
                name = r.get("name") if isinstance(r, dict) else r.data().get("name")
                if name:
                    existing_names.add(name)
        log_nontarget("VECTOR_INDEX", f"기존 인덱스 {len(existing_names)}개: {existing_names}")
    except Exception as e:
        log_nontarget("VECTOR_INDEX", f"인덱스 목록 조회 실패: {e}")
        existing_names = set()
    
    for idx_name, label, prop, dim, sim_fn in VECTOR_INDEX_DEFINITIONS:
        if idx_name in existing_names:
            log_nontarget("VECTOR_INDEX", f"인덱스 이미 존재: {idx_name}")
            continue
        
        # 벡터 인덱스 생성
        create_cypher = (
            f"CREATE VECTOR INDEX {idx_name} IF NOT EXISTS "
            f"FOR (n:{label}) ON (n.{prop}) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: '{sim_fn}'}}}}"
        )
        
        try:
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    await ctx.client.execute_with_params(create_cypher, {})
            else:
                await ctx.client.execute_with_params(create_cypher, {})
            
            created.append(idx_name)
            log_nontarget("VECTOR_INDEX", f"인덱스 생성 완료: {idx_name} ({label}.{prop})")
        except Exception as e:
            log_nontarget("VECTOR_INDEX", f"인덱스 생성 실패 ({idx_name}): {e}")
    
    log_nontarget("VECTOR_INDEX", f"벡터 인덱스 확인 완료: {len(created)}개 생성")
    return created


def create_semantic_search_tool(ctx: Any) -> StructuredTool:
    """summary 임베딩 기반 의미 검색 도구 생성 (진정한 벡터 검색)
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        StructuredTool
    """
    
    async def semantic_search_summary(
        query: Annotated[str, "검색 질문/쿼리 텍스트. 자연어 질문 또는 키워드 조합"],
        top_k: Annotated[int, "반환할 최대 결과 수 (기본: 30)"] = 30,
        label_filter: Annotated[Optional[str], "노드 레이블 필터 (예: 'NONTARGET_BLOCK', 'METHOD')"] = None,
        min_score: Annotated[float, "최소 유사도 점수 (기본: 0.7)"] = 0.7,
    ) -> str:
        """summary_vec 기반 진정한 벡터 의미 검색을 수행합니다.
        
        1. 쿼리를 OpenAI 임베딩으로 변환
        2. Neo4j 벡터 인덱스로 유사 노드 검색
        3. score가 min_score 이상인 결과만 반환
        
        예시:
        - "데이터베이스 연결 설정" → DB 관련 설정 블록
        - "주문 상태 변경 처리" → 주문 처리 메서드
        """
        if not ctx or not ctx.client:
            return json.dumps({"error": "Neo4j 클라이언트 없음"}, ensure_ascii=False)
        
        start_time = time.time()
        error_msg = None
        result_data = None
        
        try:
            # 1. 쿼리 임베딩 생성
            api_key = getattr(settings, 'openai_api_key', None)
            if not api_key:
                return json.dumps({"error": "OpenAI API 키 없음 - 벡터 검색 불가"}, ensure_ascii=False)
            
            openai_client = AsyncOpenAI(api_key=api_key)
            embedding_client = EmbeddingClient(openai_client)
            
            query_embedding = await embedding_client.embed_text(query)
            if not query_embedding:
                return json.dumps({"error": "쿼리 임베딩 생성 실패"}, ensure_ascii=False)
            
            # 2. Neo4j 벡터 인덱스 검색
            async def _query_index(index_name: str) -> List[dict]:
                """주어진 인덱스에서 벡터 검색을 수행합니다."""
                cypher = f"""
                    CALL db.index.vector.queryNodes('{index_name}', $top_k, $embedding)
                    YIELD node, score
                    WHERE score >= $min_score
                    RETURN elementId(node) AS node_id, 
                           labels(node)[0] AS type,
                           node.name AS name,
                           node.summary AS summary,
                           node.file_path AS file_path,
                           score
                    ORDER BY score DESC
                """
                
                params = {
                    "embedding": query_embedding,
                    "top_k": top_k,
                    "min_score": min_score,
                }
                
                if ctx.cypher_lock:
                    async with ctx.cypher_lock:
                        result = await ctx.client.execute_with_params(cypher, params)
                else:
                    result = await ctx.client.execute_with_params(cypher, params)
                
                if not result:
                    return []
                return [dict(r) if isinstance(r, dict) else r.data() for r in result]
            
            # label_filter 파라미터는 하위 호환성을 위해 존재하지만,
            # 실제 검색은 항상 모든 인덱스(METHOD + NONTARGET_BLOCK)를 대상으로 수행합니다.
            if label_filter:
                log_nontarget(
                    "TOOL",
                    f"semantic_search_summary: label_filter='{label_filter}'는 현재 무시되고, "
                    "모든 인덱스(METHOD + NONTARGET_BLOCK)를 대상으로 검색합니다.",
                )
            
            aggregated: List[dict] = []
            for index_name, _, _, _, _ in VECTOR_INDEX_DEFINITIONS:
                try:
                    part = await _query_index(index_name)
                    aggregated.extend(part)
                except Exception as e:
                    # 일부 인덱스 실패는 전체 실패로 취급하지 않고 로그만 남김
                    log_nontarget("TOOL", f"semantic_search 인덱스 조회 실패({index_name}): {e}")
            
            # score 기준으로 정렬 후 top_k만 반환
            aggregated.sort(key=lambda x: x.get("score", 0), reverse=True)
            nodes: List[dict] = aggregated[:top_k]
            
            log_nontarget("TOOL", f"semantic_search (벡터) 완료: {len(nodes)}개 결과")
            result_data = json.dumps(nodes, ensure_ascii=False, default=str)
            
            return result_data
            
        except Exception as e:
            error_msg = str(e)
            if "vector" in str(e).lower() or "index" in str(e).lower():
                log_nontarget("TOOL", f"semantic_search 오류: 벡터 인덱스 문제 - {e}")
                return json.dumps({
                    "error": "벡터 인덱스가 없거나 손상됨. ensure_vector_indexes()를 먼저 실행하세요.",
                    "indexes_needed": [idx[0] for idx in VECTOR_INDEX_DEFINITIONS],
                }, ensure_ascii=False)
            log_nontarget("TOOL", f"semantic_search 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        
        finally:
            duration_ms = int((time.time() - start_time) * 1000) if 'start_time' in dir() else 0
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="semantic_search_summary",
                    input_data={"query": query, "top_k": top_k, "label_filter": label_filter, "min_score": min_score},
                    output_data=result_data[:500] if 'result_data' in dir() and result_data else None,
                    duration_ms=duration_ms,
                    error=error_msg if 'error_msg' in dir() else None,
                )
    
    return StructuredTool.from_function(
        name="semantic_search_summary",
        description="""summary_vec 기반 벡터 의미 검색. 자연어 질문 하나로 모든 노드 타입에서 유사 후보를 찾습니다.

검색 대상 노드 타입 (summary_vec이 있는 모든 노드):
- METHOD, CONSTRUCTOR, FIELD, VARIABLE, METHOD_CALL, NEW_INSTANCE, ASSIGNMENT (Phase 2 코드 분석 결과)
- NONTARGET_BLOCK (Phase 3 비타겟 파일 분석 결과)

동작 방식:
1. 쿼리 텍스트를 OpenAI 임베딩으로 변환 (1회)
2. 8개 벡터 인덱스를 순회하며 각각 top_k개 후보 조회
3. 모든 결과를 score 기준 내림차순 정렬 후 최종 top_k개 반환

사용 예시:
- semantic_search_summary("주문 상태 변경 처리") → METHOD, NONTARGET_BLOCK 등에서 관련 노드 반환
- semantic_search_summary("데이터베이스 연결 설정", min_score=0.6) → 임계값 낮춰 더 많은 후보 확보

주의:
- score >= 0.8: 높은 관련성 / 0.7~0.8: 중간 / 0.7 미만: 낮음
- 결과는 후보일 뿐, neo4j_cypher로 관계 추적하여 최종 확정
- label_filter 파라미터는 무시됨 (항상 전체 검색)""",
        coroutine=semantic_search_summary,
    )


def create_lex_search_tool(ctx: Any) -> StructuredTool:
    """node_code에서 정확 문자열 검색 도구 생성
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        StructuredTool
    """
    
    async def lex_search_node_code(
        substr: Annotated[str, "검색할 문자열. 설정 키, 테이블명, 클래스명 등"],
        top_k: Annotated[int, "반환할 최대 결과 수 (기본: 50)"] = 50,
        label_filter: Annotated[Optional[str], "노드 레이블 필터 (예: 'METHOD', 'CLASS')"] = None,
    ) -> str:
        """node_code에서 정확한 문자열을 검색합니다.
        
        키워드, 상수, 테이블명 등 정확한 매칭이 필요할 때 사용합니다.
        대소문자를 구분합니다.
        
        예시:
        - "order.default-currency" → 설정 키 사용처
        - "ORDER_HEADER" → 테이블 참조
        - "OrderService" → 클래스 사용처
        """
        if not ctx or not ctx.client:
            return json.dumps({"error": "Neo4j 클라이언트 없음"}, ensure_ascii=False)
        
        start_time = time.time()
        error_msg = None
        result_data = None
        
        try:
            if label_filter:
                cypher = f"""
                    MATCH (n:{label_filter})
                    WHERE n.node_code IS NOT NULL
                      AND n.node_code CONTAINS $substr
                    RETURN elementId(n) AS node_id,
                           labels(n)[0] AS type,
                           n.name AS name,
                           n.file_path AS file_path,
                           n.start_line AS start_line,
                           substring(n.node_code, 0, 500) AS code_snippet
                    LIMIT $top_k
                """
            else:
                cypher = """
                    MATCH (n)
                    WHERE n.node_code IS NOT NULL
                      AND n.node_code CONTAINS $substr
                    RETURN elementId(n) AS node_id,
                           labels(n)[0] AS type,
                           n.name AS name,
                           n.file_path AS file_path,
                           n.start_line AS start_line,
                           substring(n.node_code, 0, 500) AS code_snippet
                    LIMIT $top_k
                """
            
            params = {"substr": substr, "top_k": top_k}
            
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    result = await ctx.client.execute_with_params(cypher, params)
            else:
                result = await ctx.client.execute_with_params(cypher, params)
            
            if result:
                nodes = [dict(r) if isinstance(r, dict) else r.data() for r in result]
                log_nontarget("TOOL", f"lex_search 완료: {len(nodes)}개 결과 (검색어: {substr})")
                result_data = json.dumps(nodes, ensure_ascii=False, default=str)
            else:
                result_data = json.dumps([], ensure_ascii=False)
            
            return result_data
            
        except Exception as e:
            error_msg = str(e)
            log_nontarget("TOOL", f"lex_search 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        
        finally:
            duration_ms = int((time.time() - start_time) * 1000) if 'start_time' in dir() else 0
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="lex_search_node_code",
                    input_data={"substr": substr, "top_k": top_k, "label_filter": label_filter},
                    output_data=result_data[:500] if 'result_data' in dir() and result_data else None,
                    duration_ms=duration_ms,
                    error=error_msg if 'error_msg' in dir() else None,
                )
    
    return StructuredTool.from_function(
        name="lex_search_node_code",
        description="""node_code 속성에서 정확한 문자열(CONTAINS)을 검색합니다.
semantic_search_summary로 의미적 후보를 찾은 뒤, 이 도구로 키워드 기반 2차 필터링에 사용합니다.

=== 검색 대상 ===
- node_code 속성이 있는 모든 노드 (METHOD, CLASS, CONSTRUCTOR, FIELD, NONTARGET_BLOCK 등)
- node_code는 원본 소스 코드를 저장한 속성입니다.
- 대소문자를 구분합니다 (Case-Sensitive).

=== 3단계 검색 전략 (결과 없으면 단계별 완화) ===

1단계 - 정확한 키워드:
  lex_search_node_code(substr="ORDER_HEADER")
  → 정확히 "ORDER_HEADER"를 포함하는 코드 블록

2단계 - 부분 문자열 (키워드 축약):
  lex_search_node_code(substr="ORDER")
  → "ORDER_HEADER", "ORDER_ITEM", "OrderService" 등 모두 매칭

3단계 - 레이블 필터로 범위 축소:
  lex_search_node_code(substr="order", label_filter="METHOD")
  → METHOD 노드만 대상으로 검색

=== 결과 활용 방법 ===
- 결과의 node_id(elementId)를 neo4j_cypher에서 관계 추적에 사용
- 결과의 code_snippet으로 실제 코드 문맥 확인
- 여러 결과가 나오면 가장 관련성 높은 것을 관계 후보로 선정

=== 사용 예시 ===
- "ORDER_HEADER" → 테이블명을 직접 참조하는 코드 블록 찾기
- "spring.datasource.url" → 설정 키를 사용하는 코드 블록 찾기
- "OrderService" → 특정 클래스를 참조하는 코드 블록 찾기
- "@Entity" → JPA 엔티티 어노테이션이 있는 코드 블록 찾기
- "INSERT INTO" → SQL INSERT 문이 포함된 코드 블록 찾기

=== 주의사항 ===
- 대소문자 구분: "order"와 "ORDER"는 다른 결과 → 필요시 두 번 호출
- 결과가 0건: 키워드를 짧게 줄이거나 다른 케이스로 재시도
- 결과가 너무 많음(50+): label_filter를 추가하여 특정 노드 타입으로 제한
- 이 도구는 CONTAINS 매칭이므로, 정규식이나 퍼지 검색은 불가""",
        coroutine=lex_search_node_code,
    )

