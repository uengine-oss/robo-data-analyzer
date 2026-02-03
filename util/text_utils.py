"""ROBO Analyzer 유틸리티 모듈

핵심 유틸리티 함수:
- 토큰 계산
- 문자열/JSON 파싱
- User Story 문서 생성
- 스트리밍 이벤트 (stream_event에서 re-export)
"""

import logging
import json
import uuid
import tiktoken
from typing import Optional, Dict, List, Any, Union

# 스트리밍 유틸리티 (stream_event.py에서 import)
from util.stream_event import (
    emit_bytes,
    emit_message,
    emit_error,
    emit_data,
    emit_node_event,
    emit_relationship_event,
    emit_complete,
    build_error_body,
    stream_with_error_boundary,
)


def log_process(context: str, stage: str, message: str, level: int = logging.INFO, exc: Exception | None = None) -> None:
    """
    공통 파이프라인 로그 출력 헬퍼.
    - context: 'DBMS', 'FRAMEWORK' 등 분석 타입
    - stage: 논리적 단계 이름
    - message: 사용자 친화적 설명
    - level: logging 모듈 레벨
    - exc: 예외 객체 전달 시 스택 트레이스까지 출력
    """
    ctx = (context or "APP").upper()
    stage_text = (stage or "STAGE").upper()
    logging.log(level, f"[{ctx}:{stage_text}] {message}", exc_info=exc)


# tiktoken 인코더 초기화
ENCODER = tiktoken.get_encoding("cl100k_base")


#==============================================================================
# 문자열/JSON 유틸리티
#==============================================================================

def parse_json_maybe(data):
    """JSON 문자열을 파싱하거나 리스트/딕셔너리는 그대로 반환"""
    if isinstance(data, str):
        return json.loads(data)
    return data or []


#==============================================================================
# 스키마/테이블 파싱 유틸리티
#==============================================================================

def parse_table_identifier(qualified_table_name: str) -> tuple[str, str, str | None]:
    """'SCHEMA.TABLE@DBLINK'에서 (schema, table, dblink) 추출
    
    따옴표(", ', `, [])를 자동으로 제거합니다.
    원본 대소문자를 유지합니다 (name_case 옵션에서 변환 처리).
    예: "RWIS"."TABLE" → (RWIS, TABLE, None)
    """
    if not qualified_table_name:
        return '', '', None
    
    def strip_quotes(s: str) -> str:
        """따옴표 제거: "name", 'name', `name`, [name] 형식 처리"""
        s = s.strip()
        if len(s) >= 2:
            if (s[0] == '"' and s[-1] == '"') or \
               (s[0] == "'" and s[-1] == "'") or \
               (s[0] == '`' and s[-1] == '`'):
                return s[1:-1]
            if s[0] == '[' and s[-1] == ']':
                return s[1:-1]
        return s
    
    text = qualified_table_name.strip()
    left, _, link = text.partition('@')
    s, _, t = left.partition('.')
    
    schema_raw = strip_quotes(s.strip()) if t else ''
    table_raw = strip_quotes(t.strip()) if t else strip_quotes(left.strip())
    link_raw = strip_quotes(link.strip()) if link.strip() else None

    # 원본 대소문자 유지 (name_case 옵션에서 변환 처리)
    schema = schema_raw or ''
    table = table_raw or ''
    db_link = link_raw if link_raw else None

    return schema, table, db_link


#==============================================================================
# 코드 분석 유틸리티
#==============================================================================

def calculate_code_token(code: Union[str, Dict, List]) -> int:
    """코드 토큰 길이 계산"""
    try:
        if isinstance(code, str):
            text = code
        else:
            text = json.dumps(code, ensure_ascii=False)
        return len(ENCODER.encode(text))
    except Exception as e:
        err_msg = f"토큰 계산 도중 문제가 발생: {str(e)}"
        logging.error(err_msg)
        raise RuntimeError(err_msg)


#==============================================================================
# User Story 문서 생성 유틸리티
#==============================================================================

def generate_user_story_document(
    results: List[Dict[str, Any]],
    source_name: str = "",
    source_type: str = "프로시저"
) -> str:
    """Summary와 User Story를 포함한 상세한 마크다운 문서를 생성합니다.
    
    Args:
        results: Neo4j 쿼리 결과 리스트 (name, summary, user_stories, type 포함)
        source_name: 소스 이름 (프로젝트명 등)
        source_type: 소스 타입 ("DBMS 프로시저/함수", "Java 클래스/인터페이스" 등)
    
    Returns:
        마크다운 형식의 상세 문서 문자열
    """
    if not results:
        return ""
    
    lines = []
    
    # 헤더
    if source_name:
        lines.append(f"# {source_name} - 요구사항 분석 문서")
    else:
        lines.append("# 요구사항 분석 문서")
    lines.append("")
    lines.append(f"> {source_type}에서 도출된 상세 요약, 사용자 스토리 및 인수 조건")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📋 목차")
    lines.append("")
    lines.append("1. [프로시저/클래스별 상세 요약](#프로시저클래스별-상세-요약)")
    lines.append("2. [User Stories & Acceptance Criteria](#user-stories--acceptance-criteria)")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # 1. 프로시저/클래스별 상세 요약
    lines.append("## 프로시저/클래스별 상세 요약")
    lines.append("")
    
    for result in results:
        name = result.get("name", "")
        summary_raw = result.get("summary", "")
        result_type = result.get("type", "")
        
        if not name:
            continue
        
        # Summary 파싱 (JSON 문자열일 수 있음)
        summary = ""
        if summary_raw:
            if isinstance(summary_raw, str):
                # summary가 JSON 문자열이면 파싱, 아니면 그대로 사용
                if summary_raw.startswith('{') or summary_raw.startswith('['):
                    try:
                        summary_parsed = json.loads(summary_raw)
                        if isinstance(summary_parsed, str):
                            summary = summary_parsed
                        else:
                            raise ValueError(f"Summary JSON이 문자열이 아닙니다: {type(summary_parsed)}")
                    except (json.JSONDecodeError, TypeError) as e:
                        raise ValueError(f"Summary JSON 파싱 실패: {summary_raw[:100]}...") from e
                else:
                    summary = summary_raw
            else:
                summary = str(summary_raw)
        
        if summary:
            lines.append(f"### {name} ({result_type})")
            lines.append("")
            # Summary를 문단별로 나누어 가독성 향상
            summary_paragraphs = summary.split('\n\n')
            for para in summary_paragraphs:
                para = para.strip()
                if para:
                    lines.append(para)
                    lines.append("")
            lines.append("---")
            lines.append("")
    
    # 2. User Stories & Acceptance Criteria
    lines.append("## User Stories & Acceptance Criteria")
    lines.append("")
    
    # 모든 User Story 집계
    all_user_stories = aggregate_user_stories_from_results(results)
    
    if not all_user_stories:
        lines.append("> User Story가 도출되지 않았습니다.")
        lines.append("")
        return "\n".join(lines)
    
    # 통계 정보
    total_stories = len(all_user_stories)
    total_ac = sum(len(us.get("acceptance_criteria", [])) for us in all_user_stories)
    lines.append(f"**총 {total_stories}개의 User Story, {total_ac}개의 Acceptance Criteria가 도출되었습니다.**")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # User Stories 상세 내용
    for us_idx, us in enumerate(all_user_stories, 1):
        us_id = us.get("id", f"US-{us_idx}")
        role = us.get("role", "")
        goal = us.get("goal", "")
        benefit = us.get("benefit", "")
        
        lines.append(f"## {us_id}")
        lines.append("")
        lines.append(f"**As a** {role}")
        lines.append("")
        lines.append(f"**I want** {goal}")
        lines.append("")
        lines.append(f"**So that** {benefit}")
        lines.append("")
        
        # Acceptance Criteria
        acs = us.get("acceptance_criteria", [])
        if acs:
            lines.append("### Acceptance Criteria")
            lines.append("")
            
            for ac in acs:
                ac_id = ac.get("id", "")
                ac_title = ac.get("title", "")
                given = ac.get("given", [])
                when = ac.get("when", [])
                then = ac.get("then", [])
                
                if ac_id or ac_title:
                    title_text = f"{ac_id}. {ac_title}" if (ac_id and ac_title) else (ac_id or ac_title)
                    lines.append(f"#### {title_text}")
                    lines.append("")
                
                if given:
                    lines.append("**Given**")
                    for g in given:
                        lines.append(f"- {g}")
                    lines.append("")
                
                if when:
                    lines.append("**When**")
                    for w in when:
                        lines.append(f"- {w}")
                    lines.append("")
                
                if then:
                    lines.append("**Then**")
                    for t in then:
                        lines.append(f"- {t}")
                    lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # 푸터
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*이 문서는 {source_type} 코드 분석을 통해 자동으로 생성되었습니다.*")
    lines.append("")
    
    return "\n".join(lines)


def aggregate_user_stories_from_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """여러 분석 결과에서 User Story를 집계합니다."""
    all_stories = []
    story_id_counter = 1
    
    for result in results:
        user_stories_raw = result.get("user_stories")
        if not user_stories_raw:
            continue
        
        if isinstance(user_stories_raw, str):
            try:
                user_stories = json.loads(user_stories_raw)
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"User Story JSON 파싱 실패: {user_stories_raw[:100]}...") from e
        else:
            user_stories = user_stories_raw
        
        if not isinstance(user_stories, list):
            raise ValueError(f"User Story가 리스트 형식이 아닙니다: {type(user_stories)}")
        
        for us in user_stories:
            # Neo4j 쿼리 결과에서 null 값 필터링
            if not us or not isinstance(us, dict) or not us.get("id"):
                continue
            
            us_copy = us.copy()
            # ID가 이미 있으면 유지, 없으면 새로 생성
            if not us_copy.get("id"):
                us_copy["id"] = f"US-{story_id_counter}"
            
            # Acceptance Criteria 처리 (Neo4j에서 collect로 묶인 배열)
            acs = us_copy.get("acceptance_criteria", [])
            if acs:
                # null 값 필터링
                acs = [ac for ac in acs if ac and isinstance(ac, dict) and ac.get("id")]
                us_copy["acceptance_criteria"] = acs
                
                # AC ID 재할당 (필요시)
                for ac_idx, ac in enumerate(acs, 1):
                    if isinstance(ac, dict) and not ac.get("id"):
                        ac["id"] = f"AC-{story_id_counter}-{ac_idx}"
            
            all_stories.append(us_copy)
            story_id_counter += 1
    
    return all_stories
