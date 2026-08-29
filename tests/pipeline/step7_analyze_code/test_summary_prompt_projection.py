from __future__ import annotations

import json

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, CallOccurrenceFact
from pipeline.models.semantic_frame import (
    profile_prompt_json,
    semantic_evidence_wire_bindings,
)
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    _summary_prompt_values,
)


def test_summary_packet_keeps_one_canonical_copy_of_assigned_facts() -> None:
    fn = CodeNode(
        id="code:sample.c:work",
        signature="void work(void)",
        file_path="sample.c",
        start_line=1,
        end_line=1,
        label="FUNCTION",
        name="work",
    )
    context = AnalysisContext(
        id=fn.id,
        signature=fn.signature,
        file_path=fn.file_path,
        start_line=1,
        end_line=1,
        callees=("code:sample.c:send",),
    )
    context.extra["ast_statements"] = ()
    context.extra["ast_call_occurrences"] = (CallOccurrenceFact(
        call_id="call-1",
        line=1,
        callee="send",
        call_source="send(value)",
        argument_sources=("value",),
        source_range=(0, 11),
        callee_source="send",
        structural_role="effect",
        presence_status="active",
    ),)
    profile, specs = build_code_semantic_plan(fn, context)
    summary_specs = semantic_concern_slots(specs)["summary"]

    values = _summary_prompt_values(
        profile,
        summary_specs,
        {
            "raw_source_code": "send(value);",
            "callee_outcomes_by_id": {
                "code:sample.c:send": {
                    "callee": "int send(int value)",
                    "meaning": "값을 전송하고 성공 여부를 반환한다.",
                },
            },
        },
    )

    projected = json.loads(values["structural_profile"])
    assert "file" not in projected["target"]
    assert "lines" not in projected["target"]
    assert all("file" not in fact for fact in projected["evidence"].values())
    assigned_bindings = semantic_evidence_wire_bindings(summary_specs)
    assert set(projected["evidence"]) == set(assigned_bindings.values())
    assert sum(
        fact["kind"] == "CALL"
        for fact in projected["evidence"].values()
    ) == 1
    projected_call = next(
        fact for fact in projected["evidence"].values() if fact["kind"] == "CALL"
    )
    assert projected_call == {
        "kind": "CALL",
        "line": 1,
        "arguments": ["value"],
        "callee": "send",
        "callee_kind": "named",
        "role": "effect",
    }
    canonical_call = next(
        fact for fact in profile.evidence.values() if fact.kind == "CALL"
    )
    assert canonical_call.attributes["occurrence_id"] == "call-1"
    assert canonical_call.attributes["source_range"] == [0, 11]
    assert "occurrence_id" not in values["structural_profile"]
    assert "call-1" not in values["structural_profile"]
    assert len(values["structural_profile"]) < len(profile_prompt_json(profile))
    assert json.loads(values["callee_contexts"]) == [{
        "callee": "int send(int value)",
        "meaning": "값을 전송하고 성공 여부를 반환한다.",
    }]
    assert set(values) == {
        "code_body",
        "structural_profile",
        "applicable_slots",
        "callee_contexts",
        "external_evidence",
    }
