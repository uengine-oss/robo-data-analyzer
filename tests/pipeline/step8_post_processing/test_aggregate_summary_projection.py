from __future__ import annotations

from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.models.aggregate_semantic_frame import (
    build_aggregate_semantic_plan,
    project_aggregate_frame,
    sealed_summary_frame_payload,
)
from pipeline.models.semantic_frame import slot_id
from tests.pipeline.semantic_frame_fixtures import semantic_frame_for_node
from tests.unittest_function_cases import load_function_cases


ROOT = Path(__file__).resolve().parents[3]


def _routine_frame(
    node_id: str = "code:test.c:run",
    meaning: str = "검증된 루틴 의미",
):
    node = CodeNode(
        id=node_id,
        label="FUNCTION",
        name="run",
        signature="long run(long value)",
        params="long value",
        return_type="long",
        file_path="test.c",
        start_line=1,
        end_line=20,
    )
    frame = semantic_frame_for_node(node, meaning=meaning)
    source_slot = next(iter(frame.slots.values()))
    complete_flow_slots = {
        slot_id(node.id, "FUNCTION", role, "TARGET"): source_slot.model_copy(update={
            "slot_id": slot_id(node.id, "FUNCTION", role, "TARGET"),
            "family": "FUNCTION",
            "role": role,
            "meaning": f"{role} 의미",
        })
        for role in (
            "GUARDS_AND_BRANCHES",
            "PROCESSING_EFFECTS",
        )
    }
    rule_id = slot_id(node.id, "DECISION", "ROLE", 10)
    rule_slot = source_slot.model_copy(update={
        "slot_id": rule_id,
        "family": "DECISION",
        "role": "ROLE",
        "meaning": "aggregate 입력에 들어가면 안 되는 분기 의미",
    })
    return frame.model_copy(update={
        "slots": {**frame.slots, **complete_flow_slots, rule_id: rule_slot},
    }), rule_id


def test_aggregate_projection_preserves_selected_member_meanings_exactly_once() -> None:
    frame, rule_id = _routine_frame()
    roles = (
            "RESPONSIBILITY", "INPUT_ROLE", "GUARDS_AND_BRANCHES",
            "PROCESSING_EFFECTS", "RESULT",
    )
    profile, specs = build_aggregate_semantic_plan(
        target_id="module:test", target_type="MODULE", owner_id="module:test",
        file="test.c", start_line=1, end_line=20, family="MODULE",
        member_frames=[frame], include_responsibility=True, member_roles=roles,
    )
    projected = project_aggregate_frame(
        profile, specs, [frame], member_roles=roles, physical_name="test.c",
    )
    responsibility = next(
        slot for slot in projected.slots.values() if slot.role == "RESPONSIBILITY"
    )
    expected = [
        slot.meaning
        for role in roles
        for slot in sorted(frame.slots.values(), key=lambda item: item.slot_id)
        if slot.role == role and slot.meaning
    ]

    assert responsibility.meaning.splitlines() == expected
    assert rule_id not in next(
        fact.attributes["member_refs"]
        for fact in projected.profile.evidence.values() if fact.kind == "MODULE"
    )
    assert "aggregate 입력에 들어가면 안 되는 분기 의미" not in responsibility.meaning
    assert next(
        slot for slot in projected.slots.values() if slot.role == "DISPLAY_NAME"
    ).status == "partial"


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


def test_module_and_component_do_not_send_member_meaning_to_another_llm() -> None:
    for relative in (
        "pipeline/steps/step8_post_processing/summarize_module_step.py",
        "pipeline/steps/step8_post_processing/build_components_step.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "run_yaml_rule" not in source
        assert "aggregate_member_payloads" not in source
    for strategy in ("framework", "dbms"):
        for rule_name in ("module_summary.yaml", "component_naming.yaml"):
            assert not (ROOT / "llm" / "prompts" / strategy / rule_name).exists()


def test_aggregate_projection_rejects_duplicate_member_slot_instead_of_deduping() -> None:
    frame, _rule_id = _routine_frame()
    try:
        build_aggregate_semantic_plan(
            target_id="module:test", target_type="MODULE", owner_id="module:test",
            file="test.c", start_line=1, end_line=20, family="MODULE",
            member_frames=[frame, frame], include_responsibility=True,
            member_roles=("RESPONSIBILITY",),
        )
    except ValueError as error:
        assert "exactly once" in str(error)
    else:
        raise AssertionError("duplicate canonical member slot was silently collapsed")


def test_equal_member_meanings_are_not_text_deduplicated() -> None:
    first, _first_rule = _routine_frame("code:test.c:first", "동일한 의미")
    second, _second_rule = _routine_frame("code:test.c:second", "동일한 의미")
    profile, specs = build_aggregate_semantic_plan(
        target_id="module:test", target_type="MODULE", owner_id="module:test",
        file="test.c", start_line=1, end_line=20, family="MODULE",
        member_frames=[first, second], include_responsibility=True,
        member_roles=("RESPONSIBILITY",),
    )

    projected = project_aggregate_frame(
        profile, specs, [first, second],
        member_roles=("RESPONSIBILITY",), physical_name="test.c",
    )
    responsibility = next(
        slot for slot in projected.slots.values() if slot.role == "RESPONSIBILITY"
    )

    assert responsibility.meaning.splitlines() == ["동일한 의미", "동일한 의미"]


def test_aggregate_projection_rejects_member_without_required_role() -> None:
    frame, _rule_id = _routine_frame()
    try:
        build_aggregate_semantic_plan(
            target_id="module:test", target_type="MODULE", owner_id="module:test",
            file="test.c", start_line=1, end_line=20, family="MODULE",
            member_frames=[frame], include_responsibility=True,
            member_roles=("NOT_A_REAL_ROLE",),
        )
    except ValueError as error:
        assert "no selected semantic slot" in str(error)
    else:
        raise AssertionError("member without a required slot was silently omitted")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
