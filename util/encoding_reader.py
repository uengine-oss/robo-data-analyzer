"""텍스트 파일 인코딩 유틸리티.

인코딩 정책 (사용자 결정):
- 4단계 chain (utf-8 → utf-8-sig → cp949 → euc-kr) 순차 시도
- 비-utf-8 인코딩으로 성공 시 errlog 의무 기록 (원본 인코딩 추적 가능하게)
- 모든 인코딩 실패 시 RuntimeError + errlog
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

import aiofiles

logger = logging.getLogger(__name__)


DEFAULT_ENCODING_CANDIDATES: tuple[str, ...] = (
    "utf-8",
    "utf-8-sig",
    "cp949",
    "euc-kr",
)
_PRIMARY_ENCODING = "utf-8"


def _log_non_utf8(file_path: str | Path, encoding: str, decode_errors: list[str]) -> None:
    """비-utf-8 인코딩으로 디코딩 성공한 경우 errlog 의무 기록."""
    if encoding == _PRIMARY_ENCODING:
        return
    logger.error(
        "[ENCODING:NON_UTF8] 비-utf-8 인코딩 사용 — utf-8 디코딩 실패 후 fallback | path=%s used=%s prior_failures=%s",
        file_path, encoding, " | ".join(decode_errors) if decode_errors else "(none)",
    )


async def read_text_with_fallback(
    file_path: str,
    preferred_encodings: Sequence[str] | None = None,
) -> tuple[str, str]:
    """여러 인코딩 후보를 순차 시도하여 텍스트를 읽는다 (비동기).

    비-utf-8 로 성공한 경우 errlog 에 의무 기록 (정책: 원본 인코딩 추적 가능).

    Returns:
        (file_content, encoding_used)

    Raises:
        RuntimeError: 모든 인코딩 후보에서 디코딩 실패
    """
    candidates = tuple(preferred_encodings) if preferred_encodings else DEFAULT_ENCODING_CANDIDATES
    decode_errors: list[str] = []

    for encoding in candidates:
        try:
            async with aiofiles.open(file_path, "r", encoding=encoding) as f:
                content = await f.read()
        except UnicodeDecodeError as e:
            decode_errors.append(f"{encoding}: {e}")
            continue
        _log_non_utf8(file_path, encoding, decode_errors)
        return content, encoding

    tried = ", ".join(candidates)
    error_detail = " | ".join(decode_errors) if decode_errors else "unknown decode error"
    logger.error(
        "[ENCODING:ALL_FAIL] 모든 인코딩 후보 디코딩 실패 | path=%s tried=%s | %s",
        file_path, tried, error_detail,
    )
    raise RuntimeError(
        f"파일 디코딩 실패: {file_path} (tried: {tried}) - {error_detail}"
    )


# =========================================================================
# 동기 버전
# =========================================================================

def read_text_with_fallback_sync(
    path: str | Path,
    encodings: tuple[str, ...] = DEFAULT_ENCODING_CANDIDATES,
) -> str:
    """여러 인코딩을 순차 시도하여 텍스트 파일을 읽는다 (동기).

    비-utf-8 로 성공한 경우 errlog 에 의무 기록 (정책: 원본 인코딩 추적 가능).
    모두 실패하면 RuntimeError.
    """
    p = Path(path)
    decode_errors: list[str] = []
    for enc in encodings:
        try:
            content = p.read_text(encoding=enc)
        except Exception as e:
            decode_errors.append(f"{enc}: {type(e).__name__}: {e}")
            continue
        _log_non_utf8(path, enc, decode_errors)
        return content
    logger.error(
        "[ENCODING:ALL_FAIL] 모든 인코딩 후보 디코딩 실패 | path=%s tried=%s | %s",
        path, ",".join(encodings), " | ".join(decode_errors) if decode_errors else "(no error captured)",
    )
    raise RuntimeError(
        f"모든 인코딩 시도 실패: {path} (후보: {encodings}) | {' | '.join(decode_errors)}"
    )


def read_json_with_fallback(
    path: str | Path,
    encodings: tuple[str, ...] = DEFAULT_ENCODING_CANDIDATES,
) -> Any:
    """인코딩 fallback으로 JSON 파일을 로드한다."""
    text = read_text_with_fallback_sync(path, encodings)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(
            "[ENCODING:JSON] JSON 파싱 실패 | path=%s | %s: %s",
            path, type(e).__name__, e,
        )
        raise RuntimeError(f"JSON 로드 실패: {path}") from e
