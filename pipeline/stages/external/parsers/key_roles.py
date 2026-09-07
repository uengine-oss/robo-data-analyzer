"""조각 키의 역할 — "이 키로 내용을 가져올 수 있는 주소인가".

코드가 키로 외부 내용을 가져오려면 그 키가 **주소**여야 한다. 스키마 필드명이나 속성명은
같은 문자열이어도 주소가 아니다. 어느 자리가 주소인지는 형식마다 정해져 있으므로 여기
**데이터 표**로 둔다 — 새 형식은 표에 줄을 더하고 코드는 건드리지 않는다.

화이트리스트다. 표에 없으면 주소가 아니다(연결 안 함). FP 0 이 recall 보다 우선이므로
모르는 자리는 주소가 아닌 쪽으로 판정한다.
"""
from __future__ import annotations

from pipeline.source_languages import (
    external_addressable_attributes,
    external_addressable_child_text,
    external_addressable_kinds,
)


def is_addressable_attribute(element: str, attribute: str) -> bool:
    """``<element attribute="키">`` 의 키가 조회 주소인가."""
    return attribute.lower() in external_addressable_attributes().get(
        _local(element), frozenset()
    )


def is_addressable_child_text(parent: str, child: str) -> bool:
    """``<parent><child>키</child></parent>`` 의 키가 조회 주소인가."""
    return _local(child) in external_addressable_child_text().get(
        _local(parent), frozenset()
    )


def is_addressable_kind(semantic_kind: str) -> bool:
    """형식 전체가 주소록인가 (properties·ini 등)."""
    return semantic_kind in external_addressable_kinds()


def _local(tag: str) -> str:
    """네임스페이스 접두를 뗀 소문자 요소명."""
    return tag.rsplit(":", 1)[-1].strip().lower()
