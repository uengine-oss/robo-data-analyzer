from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.function_meaning import function_meaning_product_properties
from pipeline.execution.run_output_context import reset_run_dir, set_run_dir
from pipeline.steps.step7_analyze_code.function_meaning import finalize_function_meaning
from tests.pipeline.semantic_frame_fixtures import semantic_frame_for_node


def _function() -> CodeNode:
    return CodeNode(
        id="code:test:work",
        name="work",
        signature="work()",
        file_path="test.c",
        start_line=1,
        end_line=3,
        label="FUNCTION",
    )


def test_sufficient_function_meaning_has_no_missing_context_artifact() -> None:
    function = _function()
    function.summary = "입력을 검증하고 결과를 반환한다."
    function.stereotype = "Helper"
    function.extra["_summary_semantic_frame"] = semantic_frame_for_node(function)

    finalize_function_meaning(function)

    assert function.meaning_status == "sufficient"
    assert function.missing_context_count == 0
    assert function.missing_context_artifact_hash == ""
    assert function_meaning_product_properties(function) == {
        "summary": function.summary,
        "stereotype": "Helper",
        "meaning_status": "sufficient",
        "missing_context_count": 0,
    }


def test_insufficient_function_meaning_writes_content_addressed_detail() -> None:
    function = _function()
    function.extra["_summary_semantic_frame"] = semantic_frame_for_node(
        function,
        status="insufficient",
    )

    with tempfile.TemporaryDirectory() as temp:
        run_dir = Path(temp)
        token = set_run_dir(run_dir)
        try:
            finalize_function_meaning(function, findings=(AnalysisFinding(
                concern="summary",
                status="unverified",
                target_id=function.id,
                reasons=("whole-function summary returned no meaning",),
                evidence_rule="code_summary",
            ),))
        finally:
            reset_run_dir(token)

        artifact = (
            run_dir
            / "semantic-artifacts"
            / "function-missing-context"
            / f"{function.missing_context_artifact_hash}.json"
        )
        encoded = artifact.read_bytes()

    assert function.meaning_status == "insufficient"
    assert function.missing_context_count > 0
    assert hashlib.sha256(encoded).hexdigest() == function.missing_context_artifact_hash
    item = function_meaning_product_properties(function)
    assert item["missing_context_count"] == function.missing_context_count
    assert "summary" not in item
    assert "stereotype" not in item
    assert "semantic_frame_json" not in item
    assert "analysis_findings_json" not in item
