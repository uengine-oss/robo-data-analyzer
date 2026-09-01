"""LangChain message/tool content → 텍스트 변환 (레이어 중립 공용 헬퍼).

Agent 미들웨어와 prompt 기록기가 공유하므로 LLM client의
provider-neutral 변환 경계에 둔다(표준 라이브러리 외 의존 0).
"""
from __future__ import annotations

from typing import Any


def content_to_text(content: Any) -> str:
    """LangChain content(str | multi-part list) → 텍스트.

    list 면 텍스트 파트(str item · ``{"type": "text"}`` dict)만 모아 줄바꿈 join하고
    이미지 등 비텍스트 파트는 버린다. str 이면 그대로, 그 밖은 빈 문자열.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)
