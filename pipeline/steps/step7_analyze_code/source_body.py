"""Exact source extraction for deterministic analysis and model packets."""
from __future__ import annotations

from pipeline.models.analysis_context import AnalysisContext
from shared.files.cache import SourceFileCache


def exact_source_body(ctx: AnalysisContext, cache: SourceFileCache) -> str:
    """Return the target's unmodified physical source range.

    Parent and child ranges may overlap by design.  This function never replaces a
    child range with generated prose and never hides source lines behind a token
    threshold; concern-specific prompt builders select their exact evidence closure.
    """
    return cache.slice(ctx.file_path, ctx.start_line, ctx.end_line)
