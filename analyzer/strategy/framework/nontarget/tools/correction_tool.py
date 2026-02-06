"""수정(A/B/C) 도구

에이전트가 분석 오류를 자체적으로 감지하고 수정할 때 사용하는 도구입니다.

- A 수정: 블록 경계 재분석 (Re-chunking)
- B 수정: Summary 재생성
- C 수정: 관계(REFERS_TO) 재생성

에이전트가 자율적으로 판단하여 호출합니다. (하드코딩 임계값 없음)
"""

import json
import time
from typing import Annotated, Any, List, Optional

from langchain_core.tools import StructuredTool

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


def create_correction_a_tool(ctx: Any) -> StructuredTool:
    """A 수정 도구 생성: 블록 경계 재분석"""
    
    async def request_correction_a(
        file_path: Annotated[str, "재청킹할 파일 경로"],
        new_max_lines: Annotated[int, "새로운 최대 블록 라인 수 (기본: 80)"] = 80,
    ) -> str:
        """블록 경계가 잘못되었다고 판단될 때 호출합니다.
        
        예: 하나의 블록에 여러 의미가 섞여 있거나,
        하나의 의미가 여러 블록으로 쪼개진 경우.
        
        재블록화 후 새로운 블록 목록을 반환합니다.
        """
        if not ctx or not ctx.client:
            return json.dumps({"error": "Neo4j 클라이언트 없음"}, ensure_ascii=False)
        
        start_time = time.time()
        
        try:
            from analyzer.strategy.framework.nontarget.processors.correction_processor import ACorrection
            result = await ACorrection.correct(
                file_path=file_path,
                ctx=ctx,
                new_max_lines=new_max_lines,
            )
            log_nontarget("TOOL", f"correction_a 완료: {file_path}")
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            log_nontarget("TOOL", f"correction_a 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="request_correction_a",
                    input_data={"file_path": file_path, "new_max_lines": new_max_lines},
                    output_data=None,
                    duration_ms=duration_ms,
                )
    
    return StructuredTool.from_function(
        name="request_correction_a",
        description="""블록 경계 재분석 (A 수정). 청킹이 잘못되었다고 판단될 때 파일 전체를 재청킹합니다.

=== 호출 판단 기준 ===
- 하나의 블록에 서로 다른 의미의 코드가 섞임 (예: DB 설정 + 서버 설정이 한 블록)
- 하나의 논리 단위가 여러 블록으로 불필요하게 분리됨 (예: SQL 문이 2개 블록으로 쪼개짐)
- 블록 크기가 비정상 (2줄짜리 블록, 200줄짜리 블록 등)
- 블록의 block_role이 대부분 "unknown"으로 나올 때

=== 동작 방식 ===
1. 해당 file_path의 기존 NONTARGET_BLOCK을 모두 비활성화 (active=false)
2. 파일을 다시 읽고 new_max_lines 기준으로 재청킹
3. 새 블록 목록을 반환 (Neo4j에 저장은 메인 에이전트가 수행)

=== 파라미터 가이드 ===
- file_path: 재청킹할 파일의 전체 경로 (예: "src/main/resources/application.yml")
- new_max_lines: 한 블록의 최대 줄 수 (기본 80, 작게 하면 더 세밀, 크게 하면 더 통합)
  - 설정 파일: 30~50줄 권장
  - SQL 파일: 80~120줄 권장
  - XML 파일: 50~80줄 권장

=== 반환값 ===
재청킹된 블록 목록 (JSON): [{name, start_line, end_line, block_code, block_role}, ...]

=== 주의사항 ===
- 파일 전체를 재처리하므로 비용이 큼 - 확실할 때만 호출
- 재청킹 후 요약/관계도 다시 생성해야 함 (B수정, C수정 순차 필요할 수 있음)""",
        coroutine=request_correction_a,
    )


def create_correction_b_tool(ctx: Any) -> StructuredTool:
    """B 수정 도구 생성: Summary 재생성"""
    
    async def request_correction_b(
        block_id: Annotated[str, "재요약할 블록의 Neo4j elementId"],
        feedback: Annotated[str, "이전 요약의 문제점 설명"] = "",
    ) -> str:
        """블록의 요약(summary/kind/keywords)이 부정확할 때 호출합니다.
        
        예: kind가 잘못 분류되었거나,
        핵심 키워드가 누락되었거나,
        요약이 블록 내용을 정확히 반영하지 못할 때.
        
        블록 코드를 다시 읽고 요약을 재생성합니다.
        """
        if not ctx or not ctx.client:
            return json.dumps({"error": "Neo4j 클라이언트 없음"}, ensure_ascii=False)
        
        start_time = time.time()
        
        try:
            from analyzer.strategy.framework.nontarget.processors.correction_processor import BCorrection
            result = await BCorrection.correct(
                block_id=block_id,
                block_code="",  # 블록 코드는 Neo4j에서 조회
                file_type="unknown",
                ctx=ctx,
                feedback=feedback,
            )
            log_nontarget("TOOL", f"correction_b 완료: {block_id}")
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            log_nontarget("TOOL", f"correction_b 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="request_correction_b",
                    input_data={"block_id": block_id, "feedback": feedback},
                    output_data=None,
                    duration_ms=duration_ms,
                )
    
    return StructuredTool.from_function(
        name="request_correction_b",
        description="""Summary/kind/keywords 재생성 (B 수정). 블록의 요약 정보가 부정확할 때 호출합니다.

=== 호출 판단 기준 ===
- kind 오분류: config 블록인데 "query"로 분류, 또는 entity-definition인데 "config"로 분류
- 키워드 누락: 블록에 ORDER_HEADER 테이블 참조가 있는데 keywords에 없음
- 요약 부정확: summary가 블록 내용을 제대로 반영하지 못함
- access_mode 오류: SELECT만 있는데 "w"로 설정됨
- 테이블/컬럼 정보 누락: entity-definition 블록인데 테이블 정보가 요약에 없음

=== kind 분류 기준 (올바른 값) ===
- "config": 설정/환경변수 (application.yml, .properties 등)
- "query": SQL 쿼리, MyBatis mapper (SELECT/INSERT/UPDATE/DELETE)
- "entity-definition": 테이블/엔티티 정의 (DDL, JPA @Entity, OFBiz entitymodel)
- "api-endpoint": REST API 정의 (controller, routing)
- "dependency": 의존성 설정 (pom.xml, build.gradle)
- "script": 스크립트/배치 (shell, SQL 스크립트)
- "documentation": 문서/주석
- "unknown": 분류 불가

=== 파라미터 가이드 ===
- block_id: 재요약할 블록의 Neo4j elementId (예: "4:abc-def:123")
- feedback: 이전 요약의 구체적 문제점 (필수 권장)
  예: "kind가 config인데 query로 분류됨. 블록에 SELECT 문이 없고 spring.datasource 설정만 있음"
  예: "키워드에 ORDER_ITEM 테이블이 누락됨. 5번째 줄에 ORDER_ITEM 참조 있음"

=== 반환값 ===
재생성된 요약 정보 (JSON): {summary, kind, keywords, access_mode, confidence}

=== 주의사항 ===
- feedback을 구체적으로 줄수록 재요약 품질이 높아짐
- 블록 코드는 Neo4j에서 자동 조회됨 (별도 전달 불필요)""",
        coroutine=request_correction_b,
    )


def create_correction_c_tool(ctx: Any) -> StructuredTool:
    """C 수정 도구 생성: 관계 재생성"""
    
    async def request_correction_c(
        block_id: Annotated[str, "관계를 수정할 블록의 Neo4j elementId"],
        remove_link_ids: Annotated[str, "제거할 관계 elementId 목록 (JSON 배열 문자열)"] = "[]",
        new_keywords: Annotated[str, "새로운 검색 키워드 목록 (JSON 배열 문자열)"] = "[]",
    ) -> str:
        """블록의 관계(REFERS_TO 등)가 잘못 연결되었을 때 호출합니다.
        
        예: 관련 없는 노드에 REFERS_TO가 연결되었거나,
        실제로 참조하는 노드에 관계가 없을 때.
        
        기존 관계를 삭제하고 새 키워드로 재탐색합니다.
        """
        if not ctx or not ctx.client:
            return json.dumps({"error": "Neo4j 클라이언트 없음"}, ensure_ascii=False)
        
        start_time = time.time()
        
        try:
            parsed_remove = json.loads(remove_link_ids) if remove_link_ids else []
            parsed_keywords = json.loads(new_keywords) if new_keywords else []
        except json.JSONDecodeError:
            return json.dumps({"error": "JSON 파싱 실패: remove_link_ids와 new_keywords는 JSON 배열이어야 합니다"}, ensure_ascii=False)
        
        try:
            from analyzer.strategy.framework.nontarget.processors.correction_processor import CCorrection
            
            new_links = [{"keyword": kw} for kw in parsed_keywords]
            result = await CCorrection.correct(
                block_id=block_id,
                remove_link_ids=parsed_remove,
                new_links=new_links,
                ctx=ctx,
            )
            log_nontarget("TOOL", f"correction_c 완료: {block_id}")
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            log_nontarget("TOOL", f"correction_c 오류: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            if hasattr(ctx, 'trace_logger') and ctx.trace_logger:
                ctx.trace_logger.log_tool_call(
                    tool_name="request_correction_c",
                    input_data={"block_id": block_id, "remove_link_ids": remove_link_ids, "new_keywords": new_keywords},
                    output_data=None,
                    duration_ms=duration_ms,
                )
    
    return StructuredTool.from_function(
        name="request_correction_c",
        description="""관계(REFERS_TO/FROM/WRITES) 재생성 (C 수정). 블록의 관계가 잘못 연결되었을 때 호출합니다.

=== 호출 판단 기준 ===
- 잘못된 관계: NONTARGET_BLOCK이 전혀 관련 없는 Table에 REFERS_TO로 연결됨
- 누락된 관계: 블록에 ORDER_HEADER 참조가 있는데 해당 Table 노드에 관계가 없음
- 관계 타입 오류: SELECT만 하는 블록이 WRITES로 연결됨 (FROM이어야 함)
- 새 키워드 발견: 이전에 못 찾았던 테이블/클래스 참조를 새로 발견함

=== 동작 방식 ===
1. remove_link_ids에 지정된 관계를 Neo4j에서 삭제
2. new_keywords로 새로운 관계 후보를 탐색 (semantic_search + lex_search)
3. 찾은 후보에 대해 새 관계를 생성

=== 파라미터 가이드 ===
- block_id: 관계를 수정할 블록의 Neo4j elementId (예: "4:abc-def:123")

- remove_link_ids: 제거할 관계 elementId를 JSON 배열 문자열로 전달
  예: '["4:abc:100", "4:abc:101"]'
  빈 배열이면 삭제 없이 추가만: '[]'
  
  관계 elementId 조회 방법:
  neo4j_cypher("MATCH (b)-[r:REFERS_TO]->(t) WHERE elementId(b) = $bid RETURN elementId(r), t.name", {bid: "..."})

- new_keywords: 새로운 검색 키워드를 JSON 배열 문자열로 전달
  예: '["ORDER_HEADER", "OrderService", "customer_id"]'
  각 키워드로 semantic_search + lex_search + neo4j 관계 추적을 수행

=== 사용 예시 ===
1. 잘못된 관계 제거만:
   request_correction_c(block_id="4:abc:1", remove_link_ids='["4:rel:99"]', new_keywords='[]')

2. 새 관계 추가만:
   request_correction_c(block_id="4:abc:1", remove_link_ids='[]', new_keywords='["ORDER_HEADER"]')

3. 교체 (삭제 + 추가):
   request_correction_c(block_id="4:abc:1", remove_link_ids='["4:rel:99"]', new_keywords='["ORDER_ITEM"]')

=== 반환값 ===
수정 결과 (JSON): {removed_count, new_links: [{target_id, keyword, match_type}], ...}

=== 주의사항 ===
- JSON 배열 문자열 형식을 정확히 지켜야 함 (작은따옴표로 감싼 JSON)
- 관계 삭제는 되돌릴 수 없으므로 확실할 때만 remove_link_ids에 추가""",
        coroutine=request_correction_c,
    )

