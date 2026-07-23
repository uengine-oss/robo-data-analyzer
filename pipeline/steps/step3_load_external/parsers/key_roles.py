"""조각 키의 역할 — "이 키로 내용을 가져올 수 있는 주소인가" (spec 081).

코드가 키로 외부 내용을 가져오려면 그 키가 **주소**여야 한다. 스키마 필드명이나 속성명은
같은 문자열이어도 주소가 아니다. 어느 자리가 주소인지는 형식마다 정해져 있으므로 여기
**데이터 표**로 둔다 — 새 형식은 표에 줄을 더하고 코드는 건드리지 않는다.

화이트리스트다. 표에 없으면 주소가 아니다(연결 안 함). FP 0 이 recall 보다 우선이므로
모르는 자리는 주소가 아닌 쪽으로 판정한다.
"""
from __future__ import annotations


# 요소명 → 그 요소에서 주소가 되는 속성 이름들.
_ADDRESSABLE_ATTRIBUTES: dict[str, frozenset[str]] = {
    # Spring / 일반 DI 컨테이너
    "bean": frozenset({"id"}),
    "alias": frozenset({"alias"}),
    # iBATIS / MyBatis 매퍼 — statement id 가 SQL 본문의 주소다
    "select": frozenset({"id"}),
    "insert": frozenset({"id"}),
    "update": frozenset({"id"}),
    "delete": frozenset({"id"}),
    "statement": frozenset({"id"}),
    "procedure": frozenset({"id"}),
    "sql": frozenset({"id"}),
    "resultmap": frozenset({"id"}),
    "parametermap": frozenset({"id"}),
    # 서비스 정의 — 코드가 이름으로 호출한다
    "service": frozenset({"name"}),
    "operation": frozenset({"name"}),
}

# 부모 요소명 → 그 아래에서 주소가 되는 **자식 요소의 텍스트**.
# 같은 태그(`<ejb-name>`)라도 정의 자리(`<session>`)면 주소, 언급 자리(`<method>`)면 아니다.
_ADDRESSABLE_CHILD_TEXT: dict[str, frozenset[str]] = {
    # J2EE 배포서술자 — 빈 정의
    "session": frozenset({"ejb-name"}),
    "entity": frozenset({"ejb-name"}),
    "message-driven": frozenset({"ejb-name"}),
    # 서블릿 컨테이너 — 정의 자리
    "servlet": frozenset({"servlet-name"}),
    "filter": frozenset({"filter-name"}),
    "listener": frozenset({"listener-class"}),
}

# 형식 자체가 "키=값 주소록"인 것들. 키 전부가 주소다.
_ADDRESSABLE_KINDS: frozenset[str] = frozenset({
    "properties_entry",
    "ini_entry",
})


def is_addressable_attribute(element: str, attribute: str) -> bool:
    """``<element attribute="키">`` 의 키가 조회 주소인가."""
    return attribute.lower() in _ADDRESSABLE_ATTRIBUTES.get(_local(element), frozenset())


def is_addressable_child_text(parent: str, child: str) -> bool:
    """``<parent><child>키</child></parent>`` 의 키가 조회 주소인가."""
    return _local(child) in _ADDRESSABLE_CHILD_TEXT.get(_local(parent), frozenset())


def is_addressable_kind(semantic_kind: str) -> bool:
    """형식 전체가 주소록인가 (properties·ini 등)."""
    return semantic_kind in _ADDRESSABLE_KINDS


def _local(tag: str) -> str:
    """네임스페이스 접두를 뗀 소문자 요소명."""
    return tag.rsplit(":", 1)[-1].strip().lower()
