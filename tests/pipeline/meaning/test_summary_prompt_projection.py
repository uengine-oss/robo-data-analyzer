from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import (
    AnalysisContext,
    CallArgumentFact,
    CallOccurrenceFact,
)
from pipeline.meaning.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.meaning.meaning_requests import summary_prompt_values


def test_summary_packet_replaces_fact_dump_with_compact_source_outline() -> None:
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
        arguments=(CallArgumentFact("value", (5, 5), "expression"),),
        source_range=(0, 11),
        callee_source="send",
        structural_role="effect",
        presence_status="active",
    ),)
    profile, specs = build_code_semantic_plan(fn, context)
    summary_specs = semantic_concern_slots(specs)["summary"]

    values = summary_prompt_values(
        profile,
        summary_specs,
        {
            "raw_source_code": "send(value);",
            "source_outline": "1 : FUNCTION\n  1 : CALL",
            "direct_table_usages": [],
            "referenced_symbols": [],
            "callee_effects_by_id": {
                "code:sample.c:send": {
                    "callee": "int send(int value)",
                    "returns": ["0", "-1"],
                },
            },
        },
    )

    canonical_call = next(
        fact for fact in profile.evidence.values() if fact.kind == "CALL"
    )
    assert canonical_call.attributes["occurrence_id"] == "call-1"
    assert canonical_call.attributes["source_range"] == [0, 11]
    assert "occurrence_id" not in values["summary_context"]
    assert "call-1" not in values["summary_context"]
    assert '"facts"' not in values["summary_context"]
    assert "1 : FUNCTION\n  1 : CALL" in values["summary_context"]
    assert "applicable_slots" not in values
    assert "DIRECT CALLEE EFFECTS" in values["summary_context"]
    assert '"returns":["0","-1"]' in values["summary_context"]
    assert "DIRECT TABLE ACCESS" not in values["summary_context"]
    assert "EXTERNAL SYMBOLS" not in values["summary_context"]
    assert "LINKED PROJECT CONTRACTS" not in values["summary_context"]
    assert set(values) == {
        "target_id",
        "code_body",
        "summary_context",
    }
