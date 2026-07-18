"""Resolve graph source references within the configured source boundary."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class AmbiguousSourceFileError(LookupError):
    """Raised when a source reference matches more than one file."""


def _under(path: Path, root: Path) -> bool:
    """Return whether *path* is equal to or contained by *root*."""
    normalized_path = os.path.normcase(str(path))
    normalized_root = os.path.normcase(str(root))
    return normalized_path == normalized_root or normalized_path.startswith(
        normalized_root + os.sep
    )


def resolve_source_file(file_path: str, source_root: Path) -> Optional[Path]:
    """Resolve an absolute, root-relative, or unique suffix source reference."""
    raw = file_path.strip().replace("\\", "/")
    if not raw:
        return None

    direct = Path(raw)
    if direct.is_absolute():
        resolved = direct.resolve()
        if resolved.is_file() and _under(resolved, source_root):
            return resolved

    joined = (source_root / raw.lstrip("/")).resolve()
    if joined.is_file() and _under(joined, source_root):
        return joined

    name = Path(raw).name
    matches = [path for path in source_root.rglob(name) if path.is_file()]
    if not matches:
        return None
    tail = "/" + raw.lstrip("/").lower()
    suffix = [
        path
        for path in matches
        if str(path).replace("\\", "/").lower().endswith(tail)
    ]
    if len(suffix) == 1:
        resolved = suffix[0].resolve()
        return resolved if _under(resolved, source_root) else None
    if len(matches) == 1:
        resolved = matches[0].resolve()
        return resolved if _under(resolved, source_root) else None
    raise AmbiguousSourceFileError(
        f"source path matches multiple files: requested={raw!r} candidates={len(matches)}"
    )
