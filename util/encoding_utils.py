"""텍스트 파일 인코딩 유틸리티."""

from __future__ import annotations

from typing import Sequence

import aiofiles


DEFAULT_ENCODING_CANDIDATES: tuple[str, ...] = (
    "utf-8",
    "utf-8-sig",
    "cp949",
    "euc-kr",
)


async def read_text_with_fallback(
    file_path: str,
    preferred_encodings: Sequence[str] | None = None,
) -> tuple[str, str]:
    """여러 인코딩 후보를 순차 시도하여 텍스트를 읽습니다.

    Args:
        file_path: 읽을 파일 경로
        preferred_encodings: 우선 시도할 인코딩 목록 (없으면 기본 후보 사용)

    Returns:
        (file_content, encoding_used)

    Raises:
        RuntimeError: 모든 인코딩 후보에서 디코딩 실패한 경우
    """
    candidates = tuple(preferred_encodings) if preferred_encodings else DEFAULT_ENCODING_CANDIDATES
    decode_errors: list[str] = []

    for encoding in candidates:
        try:
            async with aiofiles.open(file_path, "r", encoding=encoding) as f:
                return await f.read(), encoding
        except UnicodeDecodeError as e:
            decode_errors.append(f"{encoding}: {e}")

    tried = ", ".join(candidates)
    error_detail = " | ".join(decode_errors) if decode_errors else "unknown decode error"
    raise RuntimeError(
        f"파일 디코딩 실패: {file_path} (tried: {tried}) - {error_detail}"
    )
