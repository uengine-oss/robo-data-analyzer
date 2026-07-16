"""질의 의도에서 노드 타입 친화도 신호를 계산한다.

랭킹 가점만(게이트 불개입). 도메인·테이블명 하드코딩 0 — 코드 일반 어휘만.
"""
from __future__ import annotations


# 질의 의도 키워드 → 선호 노드 타입. 라벨(SELECT/MERGE…)은 DBMS 구문그래프,
# stereotype(Query/Validator…)은 함수단위 그래프에서 매칭 — 둘 다 코드 일반 어휘.
TYPE_HINTS = {
    "함수": {"FUNCTION"}, "트리거": {"TRIGGER"},
    "프로시저": {"PROCEDURE"}, "프로시져": {"PROCEDURE"},
    "조회": {"SELECT", "Query"}, "select": {"SELECT"}, "읽": {"SELECT", "Query"},
    "가져": {"SELECT", "Query"}, "검색": {"SELECT", "Query"},
    "삽입": {"INSERT"}, "추가": {"INSERT"}, "적재": {"INSERT"}, "insert": {"INSERT"}, "기록": {"INSERT"},
    "수정": {"UPDATE"}, "갱신": {"UPDATE", "MERGE"}, "변경": {"UPDATE"}, "update": {"UPDATE"},
    "병합": {"MERGE"}, "merge": {"MERGE"}, "반영": {"MERGE"},
    "커밋": {"COMMIT"}, "commit": {"COMMIT"}, "확정": {"COMMIT"},
    "롤백": {"ROLLBACK"}, "rollback": {"ROLLBACK"}, "되돌": {"ROLLBACK"},
    "커서": {"OPEN_CURSOR", "FETCH", "CLOSE_CURSOR", "CURSOR_VARIABLE"}, "cursor": {"OPEN_CURSOR", "FETCH"},
    "반복": {"LOOP"}, "루프": {"LOOP"}, "loop": {"LOOP"},
    "예외": {"EXCEPTION"}, "오류": {"EXCEPTION"}, "에러": {"EXCEPTION"},
    "조건": {"IF", "ELSIF"},
    "호출": {"CALL"}, "call": {"CALL"},
    "검증": {"Validator"}, "체크": {"Validator"},
}


def infer_types(query: str) -> set:
    """질의에 담긴 의도 키워드로 선호 타입 집합 추론. 없으면 빈집합(중립)."""
    q = (query or "").lower()
    wanted: set = set()
    for kw, labels in TYPE_HINTS.items():
        if kw in q:
            wanted |= labels
    return wanted


def type_affinity(node: dict, wanted: set) -> bool:
    """노드의 라벨 또는 stereotype이 선호 타입에 들면 참 (타입신호 판정 단일 진실)."""
    return bool(wanted) and (node.get("label") in wanted or node.get("stereotype") in wanted)
