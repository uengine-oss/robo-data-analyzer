"""Shared model-visible control-flow index with absolute source coordinates."""
from __future__ import annotations

from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import NoSupportedSourceError, lang_of
from pipeline.models.analysis_context import AnalysisContext
from pipeline.quality_gates.branch_inventory import build_inventory
from shared.files.cache import SourceFileCache


def control_index(
    fn: CodeNode,
    ctx: AnalysisContext,
    cache: SourceFileCache,
) -> str:
    """Render parser-owned control structure without inventing branch meaning."""
    try:
        return build_inventory(
            cache.slice(ctx.file_path, ctx.start_line, ctx.end_line),
            ctx.extra.get("control_subtree"),
            lang_of(Path(fn.file_path).suffix),
            line_offset=max(fn.start_line - 1, 0),
        ).branch_index()
    except NoSupportedSourceError:
        return "(분기 없음)"
