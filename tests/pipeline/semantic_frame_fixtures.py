"""Reusable provider-wire fixtures for frame-first Analyzer tests."""
from __future__ import annotations

from typing import Any

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.semantic_frame import (
    SemanticFrame,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
)
from pipeline.steps.step7_analyze_code.semantic_frame_planner import build_code_semantic_plan


def semantic_frame_for_node(
    node: Any,
    *,
    meaning: str = "검증된 의미",
    status: str = "sufficient",
    missing_context: list[str] | None = None,
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
    wire_model = semantic_frame_response_model(specs)
    missing = list(missing_context or ([] if status == "sufficient" else ["추가 근거"]))
    parsed = wire_model.model_validate({"slots": {
        spec.slot_id: {
            "meaning": "" if status == "insufficient" else meaning,
            "status": status,
            "missing_context": missing,
        }
        for spec in specs
    }})
    return normalize_semantic_frame_response(parsed, profile, specs)


def frame_wire_for_response_model(
    model: type,
    *,
    meaning: str,
    status: str = "sufficient",
    missing_context: list[str] | None = None,
    stereotype: str | None = None,
):
    slots_model = model.model_fields["slots"].annotation
    missing = list(missing_context or ([] if status == "sufficient" else ["추가 근거"]))
    payload = {"slots": {
        name: {
            "meaning": "" if status == "insufficient" else meaning,
            "status": status,
            "missing_context": missing,
        }
        for name in slots_model.model_fields
    }}
    if stereotype is not None:
        payload["stereotype"] = stereotype
    return model.model_validate(payload)
