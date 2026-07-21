"""Deterministic, root-confined inventory for project files."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from shared.files.paths import PathOutsideRoot, is_skipped, resolve_within
from pipeline.languages import is_supported
from pipeline.steps.step3_load_external.encoding_reader import is_binary_bytes
from pipeline.steps.step3_load_external.parsers.document_adapters import DOCUMENT_ADAPTER_SUFFIXES
from shared.files.cache import SourceFileCache, SourceFileReadError


_CONFIG_SUFFIXES = frozenset({
    ".json", ".jsonl", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".env", ".csv", ".tsv", ".yaml", ".yml",
})
_DOCUMENT_SUFFIXES = frozenset({".md", ".markdown"}) | DOCUMENT_ADAPTER_SUFFIXES


class InventorySecurityError(ValueError):
    """Inventory entry resolves outside the analysis root."""


@dataclass(frozen=True)
class InventoryItem:
    path: Path
    relpath: str
    ext: str
    category: str
    status: str
    reason: str = ""


def _category(path: Path, raw: bytes) -> tuple[str, str, str]:
    suffix = path.suffix.lower()
    # PDF/Office/HWP/images are structurally binary by design.  Extension
    # ownership must be decided before the generic binary fallback so the
    # optional adapter can report an honest status.
    if suffix in DOCUMENT_ADAPTER_SUFFIXES:
        return "document", "registered", ""
    if is_binary_bytes(raw):
        return "binary", "unanalyzed", "binary_unanalyzed"
    if is_supported(suffix):
        return "source", "registered", ""
    if suffix == ".xml":
        return "artifact_xml", "registered", ""
    if suffix in _CONFIG_SUFFIXES or path.name.lower().endswith(".env"):
        return "config", "registered", ""
    if suffix in _DOCUMENT_SUFFIXES:
        return "document", "registered", ""
    return "unknown", "unanalyzed", "unsupported_text_format"


def inventory_project(root: Path, source_cache: SourceFileCache) -> list[InventoryItem]:
    """Walk once in stable order; skip build trees and reject escaping symlinks."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    items: list[InventoryItem] = []
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        safe_dirs: list[str] = []
        for name in sorted(dir_names):
            lexical_dir = current_path / name
            relative_dir = lexical_dir.relative_to(root)
            if is_skipped(relative_dir):
                continue
            try:
                resolve_within(root, str(relative_dir))
            except PathOutsideRoot as error:
                raise InventorySecurityError(
                    f"inventory directory escapes root: {relative_dir.as_posix()}"
                ) from error
            # Never recurse through directory links: even an in-root target can
            # form a cycle or make one physical file acquire multiple identities.
            if lexical_dir.is_symlink():
                continue
            safe_dirs.append(name)
        dir_names[:] = safe_dirs
        for name in sorted(file_names):
            lexical = current_path / name
            rel = lexical.relative_to(root)
            if is_skipped(rel):
                continue
            try:
                resolved = resolve_within(root, str(rel))
            except PathOutsideRoot as error:
                raise InventorySecurityError(f"inventory path escapes root: {rel.as_posix()}") from error
            relpath = rel.as_posix()
            try:
                raw = source_cache.read_bytes(resolved)
            except SourceFileReadError:
                items.append(InventoryItem(
                    resolved, relpath, lexical.suffix.lower(), "unknown", "failed", "read_failed",
                ))
                continue
            category, status, reason = _category(lexical, raw)
            items.append(InventoryItem(resolved, relpath, lexical.suffix.lower(), category, status, reason))
    return sorted(items, key=lambda item: item.relpath)
