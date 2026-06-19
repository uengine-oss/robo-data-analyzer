"""노드 속성 저장 헬퍼 — "빈값 미저장"(spec 020).

Neo4j 는 스키마리스라 SET 안 한 속성은 노드에 없다. 빈 문자열·None 키를 빼고
``SET n += $item`` 하면 노드가 **자기 종류에 의미있는 속성만** 갖는다 (구문 노드에
framework/health 잡속성이 안 박힘). 숫자/불리언(0·False 포함)은 의미값이라 유지.
"""
from __future__ import annotations


def nonempty(props: dict) -> dict:
    """빈 문자열·None 키 제거 (0·False 등 의미값은 유지). MERGE 키(id)는 호출측이 항상 포함."""
    return {k: v for k, v in props.items() if v not in (None, "")}


def join_notes(*parts: str) -> str:
    """원본 부가 텍스트(주석·어노테이션 등)를 단일 ``notes`` 로 통합. 빈 조각은 제외."""
    return "\n".join(p.strip() for p in parts if p and p.strip())
