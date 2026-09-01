"""Reusable provider-wire fixtures for frame-first Analyzer tests."""
from __future__ import annotations

from typing import Any

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.semantic_frame import (
    SemanticFrame,
    SemanticSlot,
)
from pipeline.meaning.semantic_frame_planner import build_code_semantic_plan


def semantic_frame_for_node(
    node: Any,
    *,
    meaning: str = "검증된 의미",
    status: str = "sufficient",
) -> SemanticFrame:
    code = node if isinstance(node, CodeNode) else CodeNode(
        id=node.id,
        signature=getattr(node, "signature", node.id),
        file_path=getattr(node, "file_path", "test.c"),
        start_line=getattr(node, "start_line", 1),
        end_line=getattr(node, "end_line", 1),
        label=getattr(node, "label", "FUNCTION"),
        name=getattr(node, "name", ""),
        expression=getattr(node, "expression", ""),
    )
    context = AnalysisContext(
        id=code.id,
        signature=code.signature,
        file_path=code.file_path,
        start_line=code.start_line,
        end_line=code.end_line,
    )
    profile, specs = build_code_semantic_plan(code, context)
    missing_context = (
        () if status == "sufficient"
        else ("test fixture dependency unavailable",)
    )
    return SemanticFrame(
        target=profile.target,
        profile=profile,
        slots={
            spec.slot_id: SemanticSlot(
                slot_id=spec.slot_id,
                family=spec.family,
                role=spec.role,
                target_ref=spec.target_ref,
                evidence_refs=spec.evidence_refs,
                meaning="" if status == "insufficient" else meaning,
                status=status,
                missing_context=missing_context,
            )
            for spec in specs
        },
    )
