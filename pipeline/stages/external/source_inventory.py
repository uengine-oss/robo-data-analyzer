"""Deterministic, root-confined inventory for project files."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable

from shared.files.safe_paths import PathOutsideRoot, is_skipped, resolve_within
from pipeline.source_languages import external_file_categories, is_supported
from pipeline.stages.external.encoding_reader import is_binary_bytes
from pipeline.stages.external.parsers.document_adapters import DOCUMENT_ADAPTER_SUFFIXES
from shared.files.source_cache import SourceFileCache, SourceFileReadError


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
    suffix_categories, name_categories = external_file_categories()
    category = name_categories.get(path.name.lower()) or suffix_categories.get(suffix)
    if category is not None:
        return category, "registered", ""
    return "unknown", "unanalyzed", "unsupported_text_format"


def inventory_project(
    root: Path,
    source_cache: SourceFileCache,
    *,
    selected_source_paths: Iterable[Path] | None = None,
    excluded_roots: Iterable[Path] = (),
) -> list[InventoryItem]:
    """Inventory the exact analysis scope once in stable order.

    A partial upload keeps every non-source project input, but parser-supported
    source files outside ``selected_source_paths`` are not inputs and are never
    opened.  ``None`` means the complete source population; an empty iterable
    therefore means an explicitly empty source population.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    selected = (
        None
        if selected_source_paths is None
        else frozenset(Path(path).resolve() for path in selected_source_paths)
    )
    if selected is not None:
        for path in selected:
            try:
                path.relative_to(root)
            except ValueError as error:
                raise InventorySecurityError(
                    f"selected source escapes inventory root: {path}"
                ) from error
            if not path.is_file() or not is_supported(path.suffix):
                raise InventorySecurityError(
                    f"selected source is not a supported file: {path}"
                )
    excluded = tuple(Path(path).resolve() for path in excluded_roots)
    for excluded_root in excluded:
        if excluded_root == root or excluded_root in root.parents:
            raise InventorySecurityError(
                "excluded output root contains the complete inventory root: "
                f"inventory={root} excluded={excluded_root}"
            )

    def is_excluded(path: Path) -> bool:
        resolved = path.resolve()
        return any(
            resolved == excluded_root or excluded_root in resolved.parents
            for excluded_root in excluded
        )

    items: list[InventoryItem] = []
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        safe_dirs: list[str] = []
        for name in sorted(dir_names):
            lexical_dir = current_path / name
            relative_dir = lexical_dir.relative_to(root)
            if is_skipped(relative_dir):
                continue
            if is_excluded(lexical_dir):
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
            if is_excluded(lexical):
                continue
            try:
                resolved = resolve_within(root, str(rel))
            except PathOutsideRoot as error:
                raise InventorySecurityError(f"inventory path escapes root: {rel.as_posix()}") from error
            relpath = rel.as_posix()
            if (
                selected is not None
                and is_supported(lexical.suffix)
                and resolved not in selected
            ):
                continue
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
