"""Strict byte-to-text boundary for external project files.

Detection is deterministic and never treats a Latin-1 escape hatch as successful
Korean decoding: BOM, strict UTF, charset-normalizer, strict CP949/EUC-KR, then an
explicit failure.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass
import unicodedata
from pathlib import Path

from charset_normalizer import from_bytes

from shared.files.cache import SourceFileCache


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str
    had_bom: bool = False


class TextDecodeError(UnicodeError):
    """Bytes are neither supported text nor a safely detected encoding."""


_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_SAFE_DETECTED = frozenset({
    "ascii", "utf_8", "utf_16", "utf_16_be", "utf_16_le",
    "utf_32", "utf_32_be", "utf_32_le", "cp949", "euc_kr",
})


def is_binary_bytes(raw: bytes) -> bool:
    """Conservatively identify binary payloads without decoding them as text."""
    if not raw:
        return False
    if b"\x00" in raw:
        return True
    controls = sum(byte < 32 and byte not in (9, 10, 12, 13) for byte in raw)
    return controls / len(raw) > 0.10


def _clean_text(text: str) -> bool:
    if "\ufffd" in text or "\x00" in text:
        return False
    if not text:
        return True
    controls = sum(
        unicodedata.category(ch) == "Cc" and ch not in "\t\n\r\f"
        for ch in text
    )
    return controls / len(text) <= 0.02


def decode_bytes(raw: bytes) -> DecodedText:
    if not raw:
        return DecodedText("", "utf-8")
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError as error:
                raise TextDecodeError(f"invalid {encoding} BOM payload") from error
            if not _clean_text(text):
                raise TextDecodeError(f"invalid control characters in {encoding} text")
            return DecodedText(text, encoding, had_bom=True)
    if is_binary_bytes(raw):
        raise TextDecodeError("binary payload cannot be decoded as project text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if not _clean_text(text):
            raise TextDecodeError("invalid control characters in utf-8 text")
        return DecodedText(text, "utf-8")

    candidate = from_bytes(raw).best()
    if candidate is not None and candidate.encoding:
        normalized = candidate.encoding.lower().replace("-", "_")
        coherence = float(candidate.coherence or 0.0)
        if normalized in _SAFE_DETECTED or coherence >= 0.20:
            text = str(candidate)
            if _clean_text(text):
                return DecodedText(text, candidate.encoding)

    for encoding in ("cp949", "euc-kr"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _clean_text(text):
            return DecodedText(text, encoding)
    raise TextDecodeError("unsupported or low-confidence text encoding")


def read_text(path: str | Path, source_cache: SourceFileCache) -> DecodedText:
    """Decode bytes supplied by the run-scoped shared cache."""
    return decode_bytes(source_cache.read_bytes(path))
