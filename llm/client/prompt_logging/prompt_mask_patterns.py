"""정규식 패턴 — single source of truth.

시스템 프롬프트의 동적값 (timestamp / uuid) 마스킹용. 미들웨어와 단순 LLM 호출
헬퍼가 이 모듈을 공유 → DRY (절대원칙 #4).

새 패턴 추가 시 이 파일 1곳만 손댐 → 미들웨어 / message_filters / 어디서나 사용.
"""
from __future__ import annotations

import re


# ISO 8601 timestamp (선택적 분수 초 + 타임존)
TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?"
)

# UUID v4 (대소문자 무관)
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def mask_dynamic_values(text: str) -> str:
    """시스템 프롬프트의 timestamp / uuid 를 ``<TIMESTAMP>`` / ``<UUID>`` 로 마스킹.

    캐시 prefix 보존을 위해 byte-identical 강제 (Plan §3.3).
    """
    text = TIMESTAMP_RE.sub("<TIMESTAMP>", text)
    text = UUID_RE.sub("<UUID>", text)
    return text
