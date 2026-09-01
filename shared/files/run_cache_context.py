"""Run-scoped immutable file bytes shared by preflight and later consumers."""
from __future__ import annotations

import contextvars
from pathlib import Path

from shared.files.source_cache import SourceFileCache


_run_file_cache: contextvars.ContextVar[SourceFileCache | None] = contextvars.ContextVar(
    "analysis_run_file_cache", default=None,
)


def set_run_file_cache(cache: SourceFileCache) -> contextvars.Token:
    if not isinstance(cache, SourceFileCache):
        raise TypeError(f"cache must be SourceFileCache; got {type(cache).__name__}")
    return _run_file_cache.set(cache)


def reset_run_file_cache(token: contextvars.Token) -> None:
    _run_file_cache.reset(token)


def read_run_bytes(path: str | Path) -> bytes:
    """Read through the immutable run cache when active, otherwise read directly."""
    cache = _run_file_cache.get()
    if cache is not None:
        return cache.read_bytes(Path(path).expanduser().resolve())
    return Path(path).read_bytes()


def read_run_text(path: str | Path, *, encoding: str = "utf-8") -> str:
    text = read_run_bytes(path).decode(encoding, errors="strict")
    # Match Path.read_text()/TextIOWrapper's platform-independent text contract.
    # The immutable cache owns the exact bytes; text consumers historically see
    # universal newlines, and parsers must not change behaviour merely because
    # those same bytes now come from the run cache.
    return text.replace("\r\n", "\n").replace("\r", "\n")
