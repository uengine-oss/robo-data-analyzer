from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.models.aggregate_semantic_frame import (
    aggregate_member_payloads,
    build_aggregate_semantic_plan,
    sealed_summary_frame_payload,
)
from pipeline.models.semantic_frame import slot_id
from tests.pipeline.semantic_frame_fixtures import semantic_frame_for_node


def _routine_frame():
    node = CodeNode(
        id="code:test.c:run",
        label="FUNCTION",
        name="run",
        signature="long run(long value)",
        params="long value",
        return_type="long",
        file_path="test.c",
        start_line=1,
        end_line=20,
    )
    frame = semantic_frame_for_node(node, meaning="검증된 루틴 의미")
    source_slot = next(iter(frame.slots.values()))
    rule_id = slot_id(node.id, "DECISION", "ROLE", 10)
    rule_slot = source_slot.model_copy(update={
        "slot_id": rule_id,
        "family": "DECISION",
        "role": "ROLE",
        "meaning": "aggregate 입력에 들어가면 안 되는 분기 의미",
    })
    return frame.model_copy(update={
        "slots": {**frame.slots, rule_id: rule_slot},
    }), rule_id


def test_aggregate_projection_excludes_full_rule_frame_payload() -> None:
    frame, rule_id = _routine_frame()

    payload = aggregate_member_payloads(
        [frame], roles=("RESPONSIBILITY", "INPUT_ROLE", "RESULT"),
    )
    rendered = str(payload)

    assert rule_id not in rendered
    assert "분기 의미" not in rendered
    assert "profile" not in rendered
    assert "evidence" not in rendered
    assert {slot["role"] for slot in payload[0]["slots"]} == {
        "RESPONSIBILITY", "INPUT_ROLE", "RESULT",
    }


def test_aggregate_plan_references_only_selected_member_slots() -> None:
    frame, rule_id = _routine_frame()

    profile, _specs = build_aggregate_semantic_plan(
        target_id="component:test",
        target_type="COMPONENT",
        owner_id="component:test",
        file="(derived)",
        start_line=0,
        end_line=0,
        family="COMPONENT",
        member_frames=[frame],
        include_responsibility=False,
        member_roles=("RESPONSIBILITY",),
    )
    member_refs = next(
        fact.attributes["member_refs"]
        for fact in profile.evidence.values()
        if fact.kind == "MODULE"
    )

    assert rule_id not in member_refs
    assert len(member_refs) == 1


def test_aggregate_consumer_never_prefers_racy_merged_frame() -> None:
    node = CodeNode(
        id="code:test.c:run",
        label="FUNCTION",
        signature="long run(void)",
        file_path="test.c",
        start_line=1,
        end_line=2,
    )
    node.extra["_summary_semantic_frame"] = {"snapshot": "summary"}
    node.extra["semantic_frame"] = {"snapshot": "summary-plus-rules"}

    assert sealed_summary_frame_payload(node) == {"snapshot": "summary"}
