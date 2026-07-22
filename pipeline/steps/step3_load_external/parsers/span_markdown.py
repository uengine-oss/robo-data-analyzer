"""Markdown span adapter (spec 078): heading-partitioned sections.

markdown-it-py (CommonMark) decides heading positions, so a ``#`` line inside
a fenced code block never splits a section and fences with blank lines stay
whole.  Sections partition the file; whitespace-only segments produce no chunk.
"""
from __future__ import annotations

from markdown_it import MarkdownIt

from pipeline.steps.step3_load_external.parsers.span_common import (
    SemanticSpan,
    line_offsets,
)


def parse_markdown_spans(source: str) -> list[SemanticSpan]:
    tokens = MarkdownIt("commonmark").parse(source)
    heading_lines = sorted({
        token.map[0] for token in tokens
        if token.type == "heading_open" and token.map
    })
    offsets = line_offsets(source)
    total_lines = len(offsets) - 1
    boundaries = [0] + [line for line in heading_lines if line > 0] + [total_lines]
    spans: list[SemanticSpan] = []
    for start_line, end_line in zip(boundaries, boundaries[1:]):
        start = offsets[start_line]
        end = offsets[end_line]
        text = source[start:end]
        if not text.strip():
            continue
        title = text.lstrip().splitlines()[0].lstrip("#").strip() if text.lstrip().startswith("#") else ""
        spans.append(SemanticSpan(
            char_start=start,
            char_end=end,
            keys=(),
            semantic_kind="md_section",
            name=title,
        ))
    return spans
