"""Neo4j 및 검색 도구 구현

DeepAgents 에이전트가 사용할 커스텀 도구들입니다.
각 도구는 RuntimeCtx를 통해 Neo4j 클라이언트와 락에 접근합니다.

도구 목록:
- neo4j_cypher: Cypher 쿼리 실행
- semantic_search_summary: summary 임베딩 기반 의미 검색
- lex_search_node_code: node_code에서 정확 문자열 검색

참고:
- 모든 도구는 async로 정의됨
- agent.ainvoke()로 호출해야 비동기 도구가 올바르게 동작
"""

import json
import time
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.tools import StructuredTool

from analyzer.strategy.framework.nontarget.nontarget_logger import log_nontarget


# ============================================================================
# Neo4j Cypher 도구
# ============================================================================

def create_neo4j_cypher_tool(ctx: Any) -> StructuredTool:
    """Neo4j Cypher 쿼리 실행 도구 생성
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        StructuredTool
    """
    
    async def neo4j_cypher(
        query: Annotated[str, "실행할 Cypher 쿼리. 파라미터는 $name 형식으로 사용"],
        params: Annotated[Optional[Dict[str, Any]], "쿼리 파라미터 (선택). 예: {\"name\": \"OrderService\"}"] = None,
    ) -> str:
        """Neo4j Cypher 쿼리를 실행합니다.
        
        검색, 조회, 관계 탐색, 데이터 생성/수정에 사용합니다.
        보안상 파라미터화된 쿼리를 권장합니다.
        
        예시:
        - 클래스 조회: MATCH (n:CLASS) WHERE n.class_name = $name RETURN n
        - 테이블 사용처: MATCH (n)-[:FROM|WRITES]->(t:Table {name: $table}) RETURN n
        - REFERS_TO 생성: MATCH (a), (b) WHERE elementId(a) = $a_id AND elementId(b) = $b_id 
                          MERGE (a)-[r:REFERS_TO {keyword: $kw}]->(b) RETURN r
        """
        if not ctx or not ctx.client:
            return json.dumps({"error": "Neo4j 클라이언트 없음"}, ensure_ascii=False)
        
        start_time = time.time()
        error_msg = None
        result_data = None
        
        try:
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    result = await ctx.client.execute_with_params(query, params or {})
            else:
                result = await ctx.client.execute_with_params(query, params or {})
            
            # 결과를 JSON 직렬화 가능하게 변환
            if result:
                serializable = []
                for record in result:
                    if hasattr(record, 'data'):
                        serializable.append(record.data())
                    elif isinstance(record, dict):
                        serializable.append(record)
                    else:
                        serializable.append(str(record))
                log_nontarget("TOOL", f"neo4j_cypher 실행 완료: {len(serializable)}개 결과")
                result_data = json.dumps(serializable, ensure_ascii=False, default=str)
            else:
                result_data = json.dumps([], ensure_ascii=False)
            
            return result_data
            
        except Exception as e:
            error_msg = str(e)
            log_nontarget("TOOL", f"neo4j_cypher 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="neo4j_cypher",
                    input_data={"query": query[:500], "params": params},
                    output_data=result_data[:500] if result_data else None,
                    duration_ms=duration_ms,
                    error=error_msg,
                )
    
    return StructuredTool.from_function(
        name="neo4j_cypher",
        description="""Neo4j Cypher 쿼리를 실행합니다. 관계 추적에 필수!

⚠️ 한 번에 끝내지 말고 결과에 따라 반복 호출하세요!

🔍 관계 추적 패턴 (반복 호출하여 끝까지 추적):

1. USES 관계 - 키워드가 어떤 변수에 담겨있는지:
   MATCH (m:METHOD)-[:USES]->(s:SymbolRef) 
   WHERE s.name CONTAINS $keyword RETURN m, s

2. CALLS 관계 - 그 메서드를 호출하는 곳:
   MATCH (caller:METHOD)-[:CALLS]->(callee:METHOD) 
   WHERE elementId(callee) = $method_id RETURN caller

3. PARENT_OF 관계 - 메서드가 속한 클래스:
   MATCH (c:CLASS)-[:PARENT_OF]->(m:METHOD) 
   WHERE elementId(m) = $method_id RETURN c

4. Table 참조 추적:
   MATCH (t:Table)<-[:REFERS_TO]-(b:NONTARGET_BLOCK)
   WHERE t.name CONTAINS $table_name RETURN b

5. 테이블명으로 검색 (대소문자 무시):
   MATCH (t:Table) WHERE toLower(t.name) CONTAINS toLower($keyword) RETURN t""",
        coroutine=neo4j_cypher,
    )


# ============================================================================
# 의미 검색 도구 (Semantic Search)
# ============================================================================

def create_semantic_search_tool(ctx: Any) -> StructuredTool:
    """summary 임베딩 기반 의미 검색 도구 생성
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        StructuredTool
    """
    
    async def semantic_search_summary(
        query: Annotated[str, "검색 질문/쿼리 텍스트. 자연어 질문 또는 키워드 조합"],
        top_k: Annotated[int, "반환할 최대 결과 수 (기본: 30)"] = 30,
        label_filter: Annotated[Optional[str], "노드 레이블 필터 (예: 'NONTARGET_BLOCK', 'METHOD')"] = None,
    ) -> str:
        """summary 임베딩 기반 의미 검색을 수행합니다.
        
        노드의 summary 텍스트와 의미적으로 유사한 노드를 찾습니다.
        후보를 빠르게 줄이는 용도로 사용하며, 최종 확정은 그래프 관계로 검증합니다.
        
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
            # 텍스트 기반 의미 검색 (summary 필드에서 키워드 매칭)
            keywords = query.split()
            
            if label_filter:
                cypher = f"""
                    MATCH (n:{label_filter})
                    WHERE n.summary IS NOT NULL
                      AND any(kw IN $keywords WHERE toLower(n.summary) CONTAINS toLower(kw))
                    RETURN elementId(n) AS node_id, 
                           labels(n)[0] AS type,
                           n.name AS name,
                           n.summary AS summary,
                           n.file_path AS file_path
                    LIMIT $top_k
                """
            else:
                cypher = """
                    MATCH (n)
                    WHERE n.summary IS NOT NULL
                      AND any(kw IN $keywords WHERE toLower(n.summary) CONTAINS toLower(kw))
                    RETURN elementId(n) AS node_id, 
                           labels(n)[0] AS type,
                           n.name AS name,
                           n.summary AS summary,
                           n.file_path AS file_path
                    LIMIT $top_k
                """
            
            params = {"keywords": keywords, "top_k": top_k}
            
            if ctx.cypher_lock:
                async with ctx.cypher_lock:
                    result = await ctx.client.execute_with_params(cypher, params)
            else:
                result = await ctx.client.execute_with_params(cypher, params)
            
            if result:
                nodes = [dict(r) if isinstance(r, dict) else r.data() for r in result]
                log_nontarget("TOOL", f"semantic_search 완료: {len(nodes)}개 결과")
                result_data = json.dumps(nodes, ensure_ascii=False, default=str)
            else:
                result_data = json.dumps([], ensure_ascii=False)
            
            return result_data
            
        except Exception as e:
            error_msg = str(e)
            log_nontarget("TOOL", f"semantic_search 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        
        finally:
            duration_ms = int((time.time() - start_time) * 1000) if 'start_time' in dir() else 0
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="semantic_search_summary",
                    input_data={"query": query, "top_k": top_k, "label_filter": label_filter},
                    output_data=result_data[:500] if 'result_data' in dir() and result_data else None,
                    duration_ms=duration_ms,
                    error=error_msg if 'error_msg' in dir() else None,
                )
    
    return StructuredTool.from_function(
        name="semantic_search_summary",
        description="""summary 임베딩 기반 의미 검색을 수행합니다.

노드의 summary 텍스트와 의미적으로 유사한 노드를 찾습니다.
후보를 빠르게 줄이는 용도로 사용하며, 최종 확정은 그래프 관계로 검증합니다.

예시:
- "데이터베이스 연결 설정" → DB 관련 설정 블록
- "주문 상태 변경 처리" → 주문 처리 메서드

참고:
- score가 0.7 미만이면 관련성이 낮을 수 있음
- 결과는 후보일 뿐, neo4j_cypher로 관계 검증 필요""",
        coroutine=semantic_search_summary,
    )


# ============================================================================
# 키워드 검색 도구 (Lexical Search)
# ============================================================================

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
        description="""node_code에서 문자열을 검색합니다. 키워드 찾기의 시작점!

🎯 사용 시나리오:
1. 블록에서 발견한 키워드로 검색 (ORDER_ITEM, datasource.url 등)
2. 결과 노드의 elementId를 neo4j_cypher에서 관계 추적에 사용
3. 결과가 없으면 조건 완화하여 재시도

📌 검색 전략:
- 첫 시도: 정확한 키워드
- 재시도 1: 부분 문자열 (ORDER → ORDER_ITEM도 매칭)
- 재시도 2: label_filter로 특정 타입만 (METHOD, CLASS)

예시:
- "ORDER_ITEM" → 테이블 참조하는 코드 찾기
- "datasource.url" → 설정 키 사용처 찾기

참고:
- 대소문자 구분함 (필요시 다른 케이스로 재시도)
- 결과가 많으면 label_filter 사용""",
        coroutine=lex_search_node_code,
    )


# ============================================================================
# 메모리 도구 (모든 에이전트 사용)
# ============================================================================

def create_read_memory_tool(ctx: Any) -> StructuredTool:
    """메모리 파일 읽기 도구 생성"""
    import os
    
    def read_memory(
        path: Annotated[str, "읽을 파일 경로 (예: mem/project/overview.md, mem/rules/chunking_xxx.json)"],
    ) -> str:
        """메모리 파일을 읽습니다.
        
        mem/ 디렉토리 아래의 파일을 읽어서 반환합니다.
        프로젝트 컨텍스트, 규칙, 실행 기록 등을 확인할 때 사용합니다.
        
        주요 경로:
        - mem/project/overview.md: 프로젝트 전체 요약
        - mem/project/topics/: 주제별 요약 (tables.md, config.md, classes.md)
        - mem/rules/: 비타겟 파일 블록화 규칙
        - mem/runs/: 실행 기록
        """
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                log_nontarget("TOOL", f"read_memory: {path} ({len(content)} chars)")
                
                # 트레이싱
                if ctx and hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                    ctx.trace_logger.log_memory_read(path, content[:500] if content else "")
                
                return content
            else:
                return json.dumps({"error": f"파일 없음: {path}"}, ensure_ascii=False)
        except Exception as e:
            log_nontarget("TOOL", f"read_memory 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
    
    return StructuredTool.from_function(
        name="read_memory",
        description="""메모리 파일을 읽습니다. ⚠️ 반드시 작업 시작 전에 호출하세요!

📋 필수 호출 경로:
- mem/project/topics/tables.md: 기존 테이블/컬럼 정보 (테이블 작업 시 필수)
- mem/project/topics/classes.md: 기존 클래스/메서드 정보
- mem/project/topics/config.md: 기존 설정 키 정보
- mem/runs/: 이전 분석 결과 참조

📌 사용 전략:
1. list_memory("mem/project/topics")로 어떤 파일이 있는지 먼저 확인
2. 관련 파일 모두 읽어서 컨텍스트 파악
3. 기존 정보와 현재 분석 결과 비교

⚠️ 파일이 없으면 에러가 아니라 빈 결과 반환됨 - 생략 가능""",
        func=read_memory,
    )


def create_write_memory_tool(ctx: Any) -> StructuredTool:
    """메모리 파일 쓰기 도구 생성"""
    import os
    
    def write_memory(
        path: Annotated[str, "쓸 파일 경로 (예: mem/runs/2024-01-01__task.md)"],
        content: Annotated[str, "파일 내용"],
    ) -> str:
        """메모리 파일에 씁니다.
        
        작업 결과, 발견 사항, 규칙 등을 저장할 때 사용합니다.
        디렉토리가 없으면 자동 생성됩니다.
        
        주요 용도:
        - mem/runs/: 실행 기록 저장
        - mem/project/topics/: 주제별 요약 업데이트
        - mem/rules/: 블록화 규칙 저장
        """
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            log_nontarget("TOOL", f"write_memory: {path} ({len(content)} chars)")
            
            # 트레이싱
            if ctx and hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_memory_write(path, content[:500] if content else "")
            
            return json.dumps({"success": True, "path": path}, ensure_ascii=False)
        except Exception as e:
            log_nontarget("TOOL", f"write_memory 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
    
    return StructuredTool.from_function(
        name="write_memory",
        description="""메모리 파일에 씁니다. ⚠️ 분석 결과를 반드시 저장하세요!

📋 저장 규칙:
- 새 테이블 발견 → mem/project/topics/tables.md 업데이트
- 새 설정 발견 → mem/project/topics/config.md 업데이트
- 기존 정보와 충돌 → 더 정확한 정보로 덮어쓰기
- 잘못된 정보 발견 → 수정 후 저장

📌 파일 형식:
- tables.md: 마크다운 형식, 테이블별 섹션으로 구분
- config.md: 설정 키별 목록
- rules/*.json: JSON 형식의 규칙

⚠️ 기존 정보가 틀렸으면 주저하지 말고 수정하세요!
디렉토리가 없으면 자동 생성됩니다.""",
        func=write_memory,
    )


def create_list_memory_tool() -> StructuredTool:
    """메모리 디렉토리 목록 조회 도구 생성"""
    import os
    
    def list_memory(
        path: Annotated[str, "조회할 디렉토리 경로 (예: mem/runs, mem/project/topics)"],
    ) -> str:
        """메모리 디렉토리의 파일 목록을 조회합니다.
        
        특정 디렉토리에 어떤 파일들이 있는지 확인할 때 사용합니다.
        """
        try:
            if os.path.isdir(path):
                files = sorted(os.listdir(path))
                log_nontarget("TOOL", f"list_memory: {path} ({len(files)} files)")
                return json.dumps({"path": path, "files": files}, ensure_ascii=False)
            else:
                return json.dumps({"error": f"디렉토리 없음: {path}"}, ensure_ascii=False)
        except Exception as e:
            log_nontarget("TOOL", f"list_memory 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
    
    return StructuredTool.from_function(
        name="list_memory",
        description="""메모리 디렉토리의 파일 목록을 조회합니다.

주요 경로:
- mem/runs: 실행 기록 목록
- mem/project/topics: 주제별 요약 목록
- mem/rules: 블록화 규칙 목록""",
        func=list_memory,
    )


# ============================================================================
# 도구 팩토리
# ============================================================================

def get_all_tools(ctx: Any) -> List[StructuredTool]:
    """모든 커스텀 도구 반환
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock, trace_logger)
        
    Returns:
        도구 리스트
    """
    return [
        create_neo4j_cypher_tool(ctx),
        create_semantic_search_tool(ctx),
        create_lex_search_tool(ctx),
        create_read_memory_tool(ctx),
        create_write_memory_tool(ctx),
        create_list_memory_tool(),
    ]


def get_tools_by_names(ctx: Any, tool_names: List[str]) -> List[StructuredTool]:
    """이름으로 도구 필터링
    
    Args:
        ctx: RuntimeCtx
        tool_names: 필요한 도구 이름 리스트
        
    Returns:
        필터링된 도구 리스트
    """
    all_tools = get_all_tools(ctx)
    return [t for t in all_tools if t.name in tool_names]
