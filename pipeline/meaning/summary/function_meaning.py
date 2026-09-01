"""Finalize one function's product meaning after all Summary validation."""
from __future__ import annotations

from collections.abc import Iterable

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.semantic_artifacts import persist_function_missing_context
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.function_meaning import derive_function_meaning_state


def finalize_function_meaning(
    function: CodeNode,
    findings: Iterable[AnalysisFinding] = (),
) -> None:
    """Set the three explicit CodeNode publication fields from canonical evidence."""
    state = derive_function_meaning_state(
        function_id=function.id,
        summary=function.summary,
        stereotype=function.stereotype,
        summary_frame=(function.extra or {}).get("_summary_semantic_frame"),
        findings=findings,
    )
    function.meaning_status = state.meaning_status
    function.missing_context_count = len(state.missing_context_items)
    function.missing_context_artifact_hash = (
        persist_function_missing_context(state)
        if state.missing_context_items
        else ""
    )
