"""Canonical raw and model-visible representations of one source range."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence


SourceStatus = Literal["available", "empty", "read_failed"]


@dataclass(frozen=True)
class SourceExcerpt:
    """One absolute, inclusive source range with raw and numbered views.

    ``raw_text`` is the only representation for deterministic source analysis.
    ``numbered_text`` is the only representation for models, logs, graph source
    properties, and source-detail APIs.  Consumers never recover raw source by
    stripping a rendered prefix.
    """

    file_path: str
    start_line: int
    end_line: int
    raw_text: str
    numbered_text: str
    status: SourceStatus
    coordinate_system: str = "absolute-file-line"

    @classmethod
    def from_lines(
        cls,
        file_path: str,
        lines: Sequence[str],
        start_line: int,
        end_line: int,
        *,
        read_failed: bool = False,
    ) -> "SourceExcerpt":
        start = max(start_line, 1)
        resolved_end = end_line if end_line > 0 else len(lines)
        selected = list(lines[start - 1 : resolved_end]) if lines else []
        raw = "\n".join(selected)
        status: SourceStatus = (
            "read_failed" if read_failed else "available" if selected else "empty"
        )
        width = max(len(str(max(resolved_end, start))), 1)
        numbered = "\n".join(
            f"{line_no:>{width}} | {text}"
            for line_no, text in enumerate(selected, start=start)
        )
        return cls(
            file_path=file_path,
            start_line=start,
            end_line=resolved_end,
            raw_text=raw,
            numbered_text=numbered,
            status=status,
        )

    def prompt_header(self) -> str:
        return (
            f"file: {self.file_path}\n"
            f"source_range: {self.start_line}-{self.end_line}\n"
            f"coordinate_system: {self.coordinate_system}\n"
            f"source_status: {self.status}"
        )


def number_text(text: str, start_line: int) -> str:
    """Render already selected raw text with absolute source coordinates."""
    lines = text.splitlines()
    if not lines:
        return ""
    start = max(start_line, 1)
    end = start + len(lines) - 1
    width = len(str(end))
    return "\n".join(
        f"{line_no:>{width}} | {line}"
        for line_no, line in enumerate(lines, start=start)
    )


def raw_node_text(node: Any) -> str:
    """Return preserved raw source without attempting to strip rendered prefixes."""
    return getattr(node, "raw_code_text", "") or getattr(node, "code_text", "")
