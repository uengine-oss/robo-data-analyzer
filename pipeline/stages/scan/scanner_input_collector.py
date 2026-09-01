"""Build the Scan Agent's complete, compact project inventory.

The prompt receives counts and manifest paths, never an arbitrarily truncated
file body or a partial path list.  The Agent reads relevant manifests and source
through the existing paginated filesystem tools.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

from pipeline.source_languages import scanner_hints


def collect_scanner_input(
    project_root: Path,
    *,
    selected_source_paths: Iterable[Path] = (),
) -> dict:
    """Return complete counts and discoverable manifest paths for one upload.

    Returns:
        {
            "extension_counts": {extension_without_dot: complete_count},
            "manifest_paths": [project_relative_path, ...],
            "scanner_category_file_counts": {category: complete_count},
        }
    """
    ext_counter: Counter = Counter()
    manifests, category_extensions = scanner_hints()
    category_file_counts: dict[str, int] = {
        category: 0 for category in category_extensions
    }

    root = project_root.resolve()
    selected = tuple(sorted(Path(path).resolve() for path in selected_source_paths))
    if selected:
        for path in selected:
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ValueError(
                    f"scanner selected source is outside project root: {path}"
                ) from error
            if not path.is_file():
                raise ValueError(f"scanner selected source is not a file: {path}")
        source_files = selected
    else:
        source_files = tuple(
            sorted(path for path in root.rglob("*") if path.is_file())
        )

    for f in source_files:
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext:
            ext_counter[ext.lstrip(".")] += 1
        for cat, exts in category_extensions.items():
            if ext in exts:
                category_file_counts[cat] += 1

    manifest_paths: list[str] = []
    for m in manifests:
        m_path = root / m
        if m_path.is_file():
            manifest_paths.append(str(m_path.relative_to(root)))

    return {
        "extension_counts": dict(sorted(
            ext_counter.items(), key=lambda item: (-item[1], item[0]),
        )),
        "manifest_paths": manifest_paths,
        "scanner_category_file_counts": category_file_counts,
    }
