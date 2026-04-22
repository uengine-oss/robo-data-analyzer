"""단일 파일 파싱 오케스트레이터.

흐름:
  detect → 라이브러리 화이트리스트?
           │ YES → 경로 1 (library_parse)
           │ NO  → 경로 2 (DeepAgent)
           │       └ 실패 시 raw_fallback
  → validate → 결과 반환

실패해도 파이프라인은 안 멈춤. {_raw_text, _status: "unparsed"} 로 포장해 반환.
"""

import logging
import time

from .detect import detect
from .flatten import flatten
from .library_parse import is_library_supported, library_parse
from .deepagent_parse import generate_parser_with_llm

logger = logging.getLogger(__name__)


def parse(path: str) -> dict:
    """단일 파일 파싱.

    Returns:
        {
          "result": dict | list,            # 파싱 결과 (실패 시 _raw_text 포함 dict)
          "flat": dict[str, str],           # 평탄화 보조
          "elapsed_sec": float,
          "tokens": int,                    # DeepAgent 호출 시에만 발생
          "strategy": "library_<fmt>" | "deepagent" | "raw_fallback",
          "format": str,
          "validation": {"ok": bool, "reason": str},
        }
    """
    start = time.time()
    logger.info("▶ parse start: %s (%d bytes)", path, _file_size(path))

    with open(path, 'rb') as f:
        data = f.read()

    fmt, encoding = detect(path, data)
    logger.info("  detect: fmt=%s, encoding=%s", fmt, encoding)

    # 바이너리 차단 (null byte 포함)
    if fmt == 'binary':
        logger.warning("  route: binary_blocked → raw_fallback")
        return _raw_fallback(data, encoding, fmt, start, reason='binary_blocked')

    parsed = None
    tokens_used = 0
    strategy = None

    # 경로 1 — 라이브러리 직행
    if is_library_supported(fmt):
        logger.info("  route: library (%s)", fmt)
        parsed = library_parse(fmt, data, encoding)
        if parsed is not None:
            strategy = f"library_{fmt}"
            logger.info("  library_parse: success")
        else:
            logger.warning("  library_parse: None (handler failed) → DeepAgent 승격")

    # 경로 2 — DeepAgent (라이브러리 미지원 or 실패)
    if parsed is None:
        logger.info("  route: deepagent (fmt=%s)", fmt)
        parsed, _parser_code, tokens_used = generate_parser_with_llm(
            path, data, fmt_hint=fmt
        )
        if parsed is not None:
            strategy = "deepagent"
            logger.info("  deepagent: success (tokens=%d)", tokens_used)
        else:
            logger.warning("  deepagent: failed (tokens=%d)", tokens_used)

    # 결과가 없으면 raw_fallback
    if parsed is None or (hasattr(parsed, '__len__') and len(parsed) == 0):
        logger.warning("  → raw_fallback (empty or no parser succeeded)")
        return _raw_fallback(data, encoding, fmt, start,
                             reason="no_parser_succeeded", tokens=tokens_used)

    elapsed = round(time.time() - start, 3)
    logger.info("◀ parse done: strategy=%s, elapsed=%ss, tokens=%d",
                strategy, elapsed, tokens_used)

    return _pack(
        result=parsed, fmt=fmt, strategy=strategy,
        tokens=tokens_used, start=start,
        validation={"ok": True, "reason": "ok"},
    )


def _file_size(path: str) -> int:
    try:
        import os
        return os.path.getsize(path)
    except Exception:
        return -1


def _raw_fallback(data: bytes, encoding: str, fmt: str, start: float,
                  reason: str = "fallback", tokens: int = 0) -> dict:
    """파싱 실패 시 원본 텍스트를 포장해서 반환. 파이프라인 안 멈추게."""
    try:
        raw_text = data.decode(encoding or 'utf-8', errors='replace')
    except Exception:
        raw_text = data.decode('utf-8', errors='replace')

    result = {
        "_raw_text": raw_text,
        "_status": "unparsed",
    }
    return _pack(
        result=result, fmt=fmt, strategy="raw_fallback",
        tokens=tokens, start=start,
        validation={"ok": False, "reason": reason},
    )


def _pack(result, fmt, strategy, tokens, start, validation) -> dict:
    flat = flatten(result) if result else {}
    return {
        "result": result,
        "flat": flat,
        "elapsed_sec": round(time.time() - start, 3),
        "tokens": tokens,
        "strategy": strategy,
        "format": fmt,
        "validation": validation,
    }
