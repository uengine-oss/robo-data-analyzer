"""User Story 문서 생성 서비스

분석 결과에서 User Story 마크다운 문서를 생성합니다.
"""

import json
from typing import Any, Dict, List


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

    all_user_stories = aggregate_user_stories_from_results(results)

    if not all_user_stories:
        lines.append("> User Story가 도출되지 않았습니다.")
        lines.append("")
        return "\n".join(lines)

    total_stories = len(all_user_stories)
    total_ac = sum(len(us.get("acceptance_criteria", [])) for us in all_user_stories)
    lines.append(f"**총 {total_stories}개의 User Story, {total_ac}개의 Acceptance Criteria가 도출되었습니다.**")
    lines.append("")
    lines.append("---")
    lines.append("")

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
            if not us or not isinstance(us, dict) or not us.get("id"):
                continue

            us_copy = us.copy()
            if not us_copy.get("id"):
                us_copy["id"] = f"US-{story_id_counter}"

            acs = us_copy.get("acceptance_criteria", [])
            if acs:
                acs = [ac for ac in acs if ac and isinstance(ac, dict) and ac.get("id")]
                us_copy["acceptance_criteria"] = acs

                for ac_idx, ac in enumerate(acs, 1):
                    if isinstance(ac, dict) and not ac.get("id"):
                        ac["id"] = f"AC-{story_id_counter}-{ac_idx}"

            all_stories.append(us_copy)
            story_id_counter += 1

    return all_stories
