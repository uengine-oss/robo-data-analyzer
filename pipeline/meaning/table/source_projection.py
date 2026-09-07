"""Exact source projection for direct table-usage meaning requests."""
from __future__ import annotations

from collections.abc import Mapping

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import exact_direct_table_ids
from pipeline.source_languages import ROUTINE_LABELS
from shared.files.source_cache import SourceFileCache


def render_table_source(
    context: AnalysisContext,
    code_by_id: Mapping[str, CodeNode],
    cache: SourceFileCache,
) -> str:
    """Render only parser/linker-owned access spans, never an arbitrary window."""
    allowed = {
        (table_id, access)
        for access in ("READ", "WRITE")
        for table_id in exact_direct_table_ids(context, access)
    }
    ranges: list[tuple[int, int]] = []
    for usage in context.data_object_usages:
        if (
            usage.resolution != "exact"
            or (usage.table_id, usage.access) not in allowed
        ):
            continue
        owner = code_by_id.get(usage.source_id)
        if owner is not None and owner.label not in ROUTINE_LABELS:
            start, end = owner.start_line, owner.end_line
        else:
            start = end = usage.evidence_line
        if start < context.start_line or end > context.end_line or end < start:
            raise ValueError(
                "table usage source range escapes target: "
                f"target={context.id!r} range=[{start},{end}] "
                f"target_range=[{context.start_line},{context.end_line}]"
            )
        ranges.append((start, end))
    if allowed and not ranges:
        raise ValueError(
            f"direct table usage has no exact source range: {context.id!r}"
        )

    merged: list[list[int]] = []
    for start, end in sorted(set(ranges)):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return "\n".join(
        cache.excerpt(context.file_path, start, end).numbered_text
        for start, end in merged
    )
