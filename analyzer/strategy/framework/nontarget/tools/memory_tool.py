"""메모리 도구

파일 기반 메모리 읽기/쓰기/목록 조회 도구입니다.
"""

import json
import os
from typing import Annotated, Any

from langchain_core.tools import StructuredTool

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import log_nontarget


def create_read_memory_tool(ctx: Any) -> StructuredTool:
    """메모리 파일 읽기 도구 생성"""
    
    def read_memory(
        path: Annotated[str, "읽을 파일 경로 (예: mem/context/overview.md, mem/patterns/chunking_xxx.json)"],
    ) -> str:
        """메모리 파일을 읽습니다.
        
        mem/ 디렉토리 아래의 파일을 읽어서 반환합니다.
        프로젝트 컨텍스트, 규칙, 실행 기록 등을 확인할 때 사용합니다.
        
        주요 경로:
        - mem/context/overview.md: 프로젝트 전체 요약
        - mem/context/topics/: 주제별 요약 (tables.md, config.md, classes.md)
        - mem/patterns/: 비타겟 파일 블록화 패턴 규칙
        - mem/working/: 현재 작업 상태 (current.md, buffer.md)
        - mem/archive/: 아카이브된 이력
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
        description="""파일 기반 메모리 읽기. 이전 분석 결과, 패턴, 작업 상태를 조회합니다.

=== 반드시 읽어야 하는 시점 ===
- 새 파일 분석 시작 전: 기존 테이블/클래스/설정 정보 확인
- 관계 연결 전: 어떤 테이블/노드가 이미 발견되었는지 확인
- 청킹 전: 동일 확장자 파일의 패턴 규칙이 있는지 확인

=== 디렉토리 구조 ===

mem/context/topics/ (주제별 누적 정보 - 가장 중요)
  ├── tables.md     : 발견된 테이블/컬럼 목록, 관계 정보, entity_name, mapping_type
  ├── classes.md    : 발견된 클래스/서비스 목록, 역할, 주요 메서드
  └── config.md     : 발견된 설정 키(spring.datasource.url 등), 값, 영향 범위

mem/patterns/ (비타겟 파일 청킹 패턴 규칙)
  ├── chunking_yaml.json  : YAML 파일 청킹 패턴 (들여쓰기 기반 경계 등)
  ├── chunking_xml.json   : XML 파일 청킹 패턴 (태그 기반 경계 등)
  └── chunking_properties.json : Properties 파일 청킹 패턴

mem/working/ (현재 진행 상태)
  ├── current.md    : 현재 처리 중인 파일, 진행도
  └── buffer.md     : 임시 메모 (분석 중 발견한 사항)

mem/archive/ (아카이브된 이력)
  └── YYYY-MM-DD__*.md : 날짜별 실행 이력

=== 사용 전략 ===
1. 먼저 list_memory("mem/context/topics")로 어떤 토픽 파일이 존재하는지 확인
2. 관련 토픽 파일을 모두 읽어서 기존 정보 파악
3. 패턴 파일이 있으면 읽어서 청킹 시 활용
4. 파일이 없으면 {"error": "파일 없음: ..."} 반환 - 정상 동작이므로 무시하고 진행

=== 주의사항 ===
- 파일이 없는 것은 에러가 아님 (아직 생성되지 않았을 뿐)
- 큰 파일은 토큰 소비에 주의 (check_context_limit으로 확인)
- 반환값은 파일 내용 그대로 (마크다운 or JSON)""",
        func=read_memory,
    )


def create_write_memory_tool(ctx: Any) -> StructuredTool:
    """메모리 파일 쓰기 도구 생성"""
    
    def write_memory(
        path: Annotated[str, "쓸 파일 경로 (예: mem/archive/2024-01-01__task.md)"],
        content: Annotated[str, "파일 내용"],
    ) -> str:
        """메모리 파일에 씁니다.
        
        작업 결과, 발견 사항, 규칙 등을 저장할 때 사용합니다.
        디렉토리가 없으면 자동 생성됩니다.
        
        주요 용도:
        - mem/context/topics/: 주제별 요약 업데이트 (tables.md, config.md)
        - mem/patterns/: 패턴 규칙 저장 (chunking_*.json)
        - mem/working/: 작업 상태 저장 (current.md, buffer.md)
        - mem/archive/: 실행 기록 아카이브
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
        description="""파일 기반 메모리 쓰기. 분석 결과와 발견 사항을 영구 저장합니다.

=== 반드시 저장해야 하는 시점 ===
- 새 테이블 발견 시: tables.md에 추가 (테이블명, entity_name, mapping_type, 컬럼 목록)
- 새 설정 키 발견 시: config.md에 추가 (키, 값, 영향 범위)
- 청킹 패턴 학습 시: patterns/chunking_*.json에 저장
- 분석 완료 시: working/current.md에 진행 상태 업데이트

=== 저장 경로별 형식 규칙 ===

1. mem/context/topics/tables.md (마크다운):
   ## ORDER_HEADER
   - entity_name: OrderHeader
   - mapping_type: mybatis
   - schema: public
   - 컬럼: order_id(PK), status, created_at, customer_id(FK→CUSTOMER)
   - 사용처: OrderService.createOrder(WRITES), OrderMapper.xml(FROM)

2. mem/context/topics/config.md (마크다운):
   ## spring.datasource.url
   - 값: jdbc:postgresql://localhost:5432/orderdb
   - 영향: DB 연결, 테이블 접근
   - 발견 파일: application.yml

3. mem/patterns/chunking_yaml.json (JSON):
   {
     "file_ext": ".yaml",
     "boundary_rules": ["top-level key 변경 시 경계"],
     "block_role_hints": {"spring.datasource": "config", "server.port": "config"},
     "learned_from": ["src/main/resources/application.yml"]
   }

4. mem/working/current.md (마크다운):
   # 현재 작업
   - 처리 중 파일: application.yml
   - 진행 상태: 청킹 완료, 요약 진행 중
   - 발견 사항: orderdb 데이터베이스 설정 발견

=== 충돌 처리 규칙 ===
- 기존 정보가 더 정확 → 기존 유지
- 새 정보가 더 상세/정확 → 덮어쓰기 (기존 내용을 먼저 read_memory로 읽고 병합)
- 잘못된 정보 발견 → 즉시 수정 (주저하지 말 것)

=== 주의사항 ===
- 덮어쓰기임: 기존 내용을 유지하려면 먼저 read_memory로 읽고 병합하여 쓸 것
- 디렉토리 자동 생성: 경로에 없는 디렉토리는 자동으로 만들어짐
- UTF-8 인코딩: 한글, 특수문자 모두 정상 처리""",
        func=write_memory,
    )


def create_list_memory_tool() -> StructuredTool:
    """메모리 디렉토리 목록 조회 도구 생성"""
    
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
        description="""메모리 디렉토리의 파일 목록을 조회합니다. read_memory 전에 먼저 호출하여 어떤 파일이 있는지 확인합니다.

=== 주요 경로 ===
- mem/context/topics : 주제별 누적 정보 (tables.md, classes.md, config.md)
- mem/patterns       : 확장자별 청킹 패턴 규칙 (chunking_yaml.json 등)
- mem/working        : 현재 작업 상태 (current.md, buffer.md)
- mem/archive        : 날짜별 아카이브 이력

=== 사용 순서 ===
1. list_memory("mem/context/topics") → 어떤 토픽 파일이 있는지 확인
2. 관련 파일이 있으면 read_memory로 내용 확인
3. 없으면 새로 생성할 준비

=== 반환 형식 ===
{"path": "mem/context/topics", "files": ["tables.md", "classes.md", "config.md"]}
- 디렉토리가 없으면: {"error": "디렉토리 없음: ..."}""",
        func=list_memory,
    )

