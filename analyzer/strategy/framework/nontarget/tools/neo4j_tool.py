"""Neo4j Cypher 도구

Neo4j Cypher 쿼리 실행 도구입니다.
"""

import json
import time
from typing import Annotated, Any, Dict, Optional

from langchain_core.tools import StructuredTool

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


def create_neo4j_cypher_tool_readonly(ctx: Any) -> StructuredTool:
    """Neo4j Cypher 쿼리 실행 도구 생성 (읽기 전용 - 서브에이전트용)
    
    CREATE, MERGE, SET, DELETE 등의 쓰기 작업은 금지됩니다.
    MATCH, RETURN만 허용됩니다.
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        StructuredTool (읽기 전용)
    """
    
    async def neo4j_cypher_readonly(
        query: Annotated[str, "실행할 Cypher 쿼리 (MATCH/RETURN만 허용, CREATE/MERGE/SET/DELETE 금지). 파라미터는 $변수명 형식 사용. 예: MATCH (t:Table) WHERE t.name = $name RETURN t"],
        params: Annotated[Optional[Dict[str, Any]], "쿼리에서 $변수로 참조하는 파라미터 딕셔너리. 예: {\"name\": \"ORDER_HEADER\", \"method_id\": \"4:abc:123\"}"] = None,
    ) -> str:
        """Neo4j Cypher 쿼리를 실행합니다 (읽기 전용).
        
        ⚠️ CREATE, MERGE, SET, DELETE는 사용할 수 없습니다.
        MATCH, RETURN만 허용됩니다.
        
        예시:
        - 클래스 조회: MATCH (n:CLASS) WHERE n.class_name = $name RETURN n
        - 테이블 사용처: MATCH (n)-[:FROM|WRITES]->(t:Table {name: $table}) RETURN n
        - 관계 추적: MATCH (m:METHOD)-[:USES]->(s:SymbolRef) WHERE s.name CONTAINS $keyword RETURN m, s
        """
        if not ctx or not ctx.client:
            return json.dumps({"error": "Neo4j 클라이언트 없음"}, ensure_ascii=False)
        
        # 쓰기 작업 금지 검증
        query_upper = query.upper().strip()
        forbidden_keywords = ['CREATE', 'MERGE', 'SET', 'DELETE', 'DETACH', 'REMOVE', 'DROP']
        for keyword in forbidden_keywords:
            if keyword in query_upper:
                error_msg = f"읽기 전용 모드: {keyword} 문은 사용할 수 없습니다. MATCH/RETURN만 허용됩니다."
                log_nontarget("TOOL", f"neo4j_cypher_readonly 거부: {keyword} 사용 시도")
                return json.dumps({"error": error_msg}, ensure_ascii=False)
        
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
                log_nontarget("TOOL", f"neo4j_cypher_readonly 실행 완료: {len(serializable)}개 결과")
                result_data = json.dumps(serializable, ensure_ascii=False, default=str)
            else:
                result_data = json.dumps([], ensure_ascii=False)
            
            return result_data
            
        except Exception as e:
            error_msg = str(e)
            log_nontarget("TOOL", f"neo4j_cypher_readonly 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="neo4j_cypher_readonly",
                    input_data={"query": query[:500], "params": params},
                    output_data=result_data[:500] if result_data else None,
                    duration_ms=duration_ms,
                    error=error_msg,
                )
    
    return StructuredTool.from_function(
        name="neo4j_cypher",
        description="""Neo4j Cypher 쿼리 실행 (읽기 전용 - 서브에이전트 전용).

=== 권한 ===
- 허용: MATCH, RETURN, WITH, WHERE, ORDER BY, LIMIT, UNWIND, OPTIONAL MATCH
- 금지: CREATE, MERGE, SET, DELETE, DETACH, REMOVE, DROP (사용 시 즉시 거부됨)
- 쓰기 작업이 필요하면 메인 에이전트에 JSON으로 반환하세요.

=== 노드 타입 및 레이블 ===
- 코드 노드: CLASS, INTERFACE, ENUM, METHOD, CONSTRUCTOR, FIELD, VARIABLE, METHOD_CALL, NEW_INSTANCE, ASSIGNMENT
- 비타겟 노드: NONTARGET_BLOCK (비정형 파일에서 추출된 블록)
- 테이블 노드: Table (DDL 스키마에서 생성), Column (테이블 컬럼)
- 참조 노드: SymbolRef (심볼 참조)

=== 주요 관계 타입 ===
- PARENT_OF: 클래스→메서드/필드 포함 관계
- CALLS: 메서드→메서드 호출 관계
- USES: 메서드→심볼참조 사용 관계
- FROM: 메서드→테이블 읽기(SELECT) 관계
- WRITES: 메서드→테이블 쓰기(INSERT/UPDATE/DELETE) 관계
- REFERS_TO: NONTARGET_BLOCK→다른 노드 참조 관계
- FK_TO: Column→Column 외래키 관계
- FK_TO_TABLE: Table→Table 외래키 테이블 관계
- HAS_COLUMN: Table→Column 포함 관계

=== 검색 패턴 (자주 사용하는 것 우선) ===

1. 테이블 관련 노드 찾기 (이름, 대소문자 무시):
   MATCH (t:Table) WHERE toLower(t.name) CONTAINS toLower($keyword) RETURN t.name, elementId(t)

2. 테이블에 연결된 모든 관계 한번에 조회:
   MATCH (n)-[r]->(t:Table) WHERE t.name = $table_name 
   RETURN type(r) AS rel_type, labels(n)[0] AS source_type, n.name AS source_name, elementId(n) AS source_id

3. 메서드가 참조하는 테이블 목록:
   MATCH (m:METHOD)-[r:FROM|WRITES]->(t:Table) WHERE elementId(m) = $method_id 
   RETURN t.name, type(r) AS access_type

4. 키워드가 포함된 심볼 참조를 사용하는 메서드:
   MATCH (m:METHOD)-[:USES]->(s:SymbolRef) WHERE s.name CONTAINS $keyword 
   RETURN m.name, elementId(m) AS method_id, s.name AS symbol_name

5. 메서드 호출 체인 추적 (caller→callee):
   MATCH (caller:METHOD)-[:CALLS]->(callee:METHOD) WHERE elementId(callee) = $method_id 
   RETURN caller.name, elementId(caller) AS caller_id, caller.file_path

6. 클래스 소속 메서드 목록:
   MATCH (c:CLASS)-[:PARENT_OF]->(m:METHOD) WHERE c.class_name = $class_name 
   RETURN m.name, elementId(m) AS method_id

7. NONTARGET_BLOCK에서 특정 노드를 참조하는 블록:
   MATCH (b:NONTARGET_BLOCK)-[r:REFERS_TO]->(target) WHERE elementId(target) = $target_id 
   RETURN b.name, b.summary, r.keyword, r.match_type

8. 테이블의 컬럼 목록:
   MATCH (t:Table)-[:HAS_COLUMN]->(c:Column) WHERE t.name = $table_name 
   RETURN c.name, c.data_type, c.description

=== Cypher 문법 주의사항 ===
- 속성 존재 확인: n.name IS NOT NULL (exists(n.name) 사용 금지 - deprecated)
- 파라미터: 항상 $name 형식 사용 (문자열 직접 삽입 금지)
- LIMIT: 대량 조회 시 반드시 LIMIT 사용 (기본 50)
- elementId(): Neo4j 5.x에서 id() 대신 elementId() 사용

=== 검색 전략 가이드 ===
- 결과가 0건이면: 검색 조건 완화 (toLower, CONTAINS 사용)
- 결과가 너무 많으면: label 제한, 추가 WHERE 조건 추가
- 관계 추적 시: 한 번에 끝내지 말고 결과 기반으로 다음 쿼리 구성""",
        coroutine=neo4j_cypher_readonly,
    )


def create_neo4j_cypher_tool(ctx: Any) -> StructuredTool:
    """[DEPRECATED] Neo4j Cypher 쿼리 실행 도구 생성 (쓰기 포함)

    ⚠️ 새 4-Step 파이프라인에서는 에이전트에 쓰기 도구를 제공하지 않습니다.
    시스템(cypher_templates.py)이 템플릿 Cypher로 저장을 담당합니다.

    이 함수는 API 라우터 등 다른 용도에서 사용될 수 있어 삭제하지 않습니다.
    에이전트용 도구는 create_neo4j_cypher_tool_readonly()를 사용하세요.
    
    Args:
        ctx: RuntimeCtx (client, cypher_lock)
        
    Returns:
        StructuredTool
    """
    
    async def neo4j_cypher(
        query: Annotated[str, "실행할 Cypher 쿼리 (MATCH/MERGE/SET 등 모두 가능). 중복 방지를 위해 MERGE 사용 권장. 파라미터는 $변수명 형식. 예: MERGE (t:Table {name: $name}) ON CREATE SET t.description = $desc"],
        params: Annotated[Optional[Dict[str, Any]], "쿼리에서 $변수로 참조하는 파라미터 딕셔너리. 예: {\"name\": \"ORDER_HEADER\", \"desc\": \"주문 헤더 테이블\"}"] = None,
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
        description="""Neo4j Cypher 쿼리 실행 (메인 에이전트 전용 - 읽기/쓰기 모두 가능).

=== 권한 ===
- 허용: MATCH, RETURN, CREATE, MERGE, SET, DELETE, WITH, WHERE, UNWIND 등 모두 가능
- 메인 에이전트만 이 도구를 가짐 (서브에이전트는 읽기 전용 버전 사용)

=== 쓰기 작업 필수 규칙 (가드레일) ===

1. 중복 방지: 반드시 MERGE 사용 (CREATE 금지)
   ✅ MERGE (n:NONTARGET_BLOCK {name: $name, file_path: $path})
   ❌ CREATE (n:NONTARGET_BLOCK {name: $name, file_path: $path})

2. 속성 설정: MERGE 후 ON CREATE SET / ON MATCH SET 분리
   MERGE (n:NONTARGET_BLOCK {name: $name, file_path: $path})
   ON CREATE SET n.summary = $summary, n.kind = $kind, n.created_at = timestamp()
   ON MATCH SET n.summary = $summary, n.kind = $kind, n.updated_at = timestamp()

3. 관계 생성: 양쪽 노드를 elementId로 매칭 후 MERGE
   MATCH (a) WHERE elementId(a) = $source_id
   MATCH (b) WHERE elementId(b) = $target_id
   MERGE (a)-[r:REFERS_TO]->(b)
   SET r.keyword = $keyword, r.match_type = $match_type, r.source_lines = $source_lines

=== NONTARGET_BLOCK 노드 생성 패턴 ===
MERGE (b:NONTARGET_BLOCK {name: $name, file_path: $file_path})
ON CREATE SET
  b.node_code = $block_code,
  b.summary = $summary,
  b.kind = $kind,
  b.keywords = $keywords,
  b.start_line = $start_line,
  b.end_line = $end_line,
  b.file_name = $file_name,
  b.file_ext = $file_ext,
  b.access_mode = $access_mode,
  b.block_role = $block_role,
  b.active = true,
  b.confidence = $confidence,
  b.created_at = timestamp()

=== Table 노드 생성/업데이트 패턴 ===
⚠️ DB 이름(orderdb, userdb 등)은 Table로 생성 금지!
⚠️ entity_name, mapping_type 속성 필수!

MERGE (t:Table {name: $table_name, schema: $schema})
ON CREATE SET
  t.entity_name = $entity_name,
  t.mapping_type = $mapping_type,
  t.description = $description,
  t.description_source = 'nontarget'
ON MATCH SET
  t.entity_name = COALESCE($entity_name, t.entity_name),
  t.mapping_type = COALESCE($mapping_type, t.mapping_type),
  t.description = CASE WHEN $description IS NOT NULL AND size($description) > size(COALESCE(t.description, ''))
                       THEN $description ELSE t.description END

=== 관계 타입별 생성 패턴 ===

FROM (읽기 관계 - access_mode에 'r' 포함 시):
  MATCH (b) WHERE elementId(b) = $block_id
  MATCH (t:Table) WHERE t.name = $table_name
  MERGE (b)-[r:FROM]->(t) SET r.evidence = $evidence

WRITES (쓰기 관계 - access_mode에 'w' 포함 시):
  MATCH (b) WHERE elementId(b) = $block_id
  MATCH (t:Table) WHERE t.name = $table_name
  MERGE (b)-[r:WRITES]->(t) SET r.evidence = $evidence

REFERS_TO (일반 참조 관계):
  MATCH (b) WHERE elementId(b) = $source_id
  MATCH (target) WHERE elementId(target) = $target_id
  MERGE (b)-[r:REFERS_TO]->(target)
  SET r.keyword = $keyword, r.match_type = $match_type,
      r.source_lines = $source_lines, r.matched_text = $matched_text,
      r.confidence = $confidence

=== 읽기 검색 패턴 (서브에이전트와 동일) ===
- 테이블 검색: MATCH (t:Table) WHERE toLower(t.name) CONTAINS toLower($keyword) RETURN t
- 관계 추적: MATCH (n)-[r]->(t:Table {name: $name}) RETURN type(r), n
- 호출 체인: MATCH (a)-[:CALLS]->(b:METHOD) WHERE elementId(b) = $id RETURN a

=== Cypher 문법 규칙 ===
- exists(n.prop) 사용 금지 → n.prop IS NOT NULL 사용
- id(n) 사용 금지 → elementId(n) 사용
- 문자열 직접 삽입 금지 → $param 파라미터 사용
- 대량 작업 시 UNWIND + MERGE 패턴 사용""",
        coroutine=neo4j_cypher,
    )

