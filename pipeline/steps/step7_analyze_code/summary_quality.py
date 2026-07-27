"""Framework/DBMS가 공유하는 독립 summary/search_text 필수값 계약."""
from __future__ import annotations


def normalize_searchable_text(summary: str, search_text: str) -> tuple[str, str]:
    """성공한 분석 노드가 사용자 설명과 검색 설명을 모두 갖게 한다.

    의미를 복사·추론하지 않고 공백 제거와 필수값 검증만 수행한다. 빈 필드는 structured
    output 검증 또는 호출자의 재요청 경로로 돌려보낸다. 언어·프로젝트·도메인 식별자를
    보지 않는 공통 계약이다.
    """
    normalized_summary = (summary or "").strip()
    normalized_search = (search_text or "").strip()
    if not normalized_summary:
        raise ValueError("summary is empty")
    if not normalized_search:
        raise ValueError("search_text is empty")
    return normalized_summary, normalized_search
