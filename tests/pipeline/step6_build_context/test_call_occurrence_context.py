from __future__ import annotations

import pytest

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import (
    AnalysisContext,
    CallOccurrenceFact,
    ControlSubtreeNode,
)
from pipeline.models.rule_expression import parse_effect
from pipeline.models.rule_materialization import (
    StructuralEffect,
    render_structural_effect,
)
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.steps.step6_build_context.build_context_step import (
    build_parser_call_evidence,
)
from pipeline.steps.step7_analyze_code.semantic_frame_planner import (
    build_code_semantic_plan,
)


def _routine() -> CodeNode:
    node = CodeNode(
        id="code:sample.c:fn@10",
        name="fn",
        signature="fn()",
        file_path="sample.c",
        start_line=10,
        end_line=20,
        label="FUNCTION",
    )
    node.extra["_resolved_expression_facts"] = ({
        "fact_id": "condition-fact",
        "source_id": "sample.c",
        "code_node_id": "if",
        "role": "condition",
        "start_line": 10,
        "end_line": 10,
        "source_range": {"char_offset": 100, "char_length": 20},
        "expression": {
            "kind": "call",
            "source_range": {"char_offset": 105, "char_length": 8},
        },
    },)
    return node


def _site(call_id: str, offset: int, source: str) -> dict:
    return {
        "call_id": call_id,
        "caller_id": "code:sample.c:fn@10",
        "owner_status": "resolved",
        "call_text": "check",
        "callee_kind": "named",
        "source_id": "sample.c",
        "file": "sample.c",
        "line": 11,
        "range": {"charOffset": offset, "charLength": len(source)},
        "callee_range": {"charOffset": offset, "charLength": 5},
        "call_source": source,
        "callee_source": "check",
        "argument_ranges": [],
        "argument_sources": [],
        "receiver_source": None,
        "receiver_range": None,
        "scope_path": [{
            "kind": "translation_unit",
            "range": {"charOffset": 0, "charLength": 1000},
        }, {
            "kind": "function",
            "range": {"charOffset": 80, "charLength": 100},
        }],
        "grammar_rule": "postfixExpression",
        "presence_status": "active",
        "presence": {"status": "active"},
    }


def _resolution(call_id: str, ordinal: int) -> dict:
    return {
        "call_id": call_id,
        "ordinal": ordinal,
        "caller_id": "code:sample.c:fn@10",
        "resolution_status": "internal",
        "adapter_schema": "test-call/v1",
        "outcome": "edge",
        "reason": "",
        "scope": "internal",
        "callee_id": "code:sample.c:check@30",
        "external_identity_key": "",
        "declaration_fact_id": "definition-check",
        "candidate_fact_ids": [],
        "preprocessing_evidence_ids": [],
        "macro_fact_ids": [],
    }


def test_exact_ranges_classify_same_line_condition_and_effect_calls() -> None:
    sites = (
        _site("condition-call", 105, "check(x)"),
        _site("effect-call", 130, "check(y)"),
    )
    resolutions = (
        _resolution("condition-call", 0),
        _resolution("effect-call", 1),
    )

    occurrences = build_parser_call_evidence(
        list(sites), list(resolutions), [_routine()],
    )
    projected = occurrences["code:sample.c:fn@10"]
    assert [item.structural_role for item in projected] == ["condition", "effect"]
    assert projected[0].condition_fact_id == "condition-fact"
    assert projected[1].condition_fact_id is None
    assert projected[0].callee_id == "code:sample.c:check@30"


def test_inactive_condition_keeps_structure_separate_from_presence() -> None:
    site = _site("inactive-condition-call", 105, "check(x)")
    site["presence_status"] = "inactive"
    site["presence"] = {"status": "inactive"}
    occurrences = build_parser_call_evidence(
        [site], [_resolution("inactive-condition-call", 0)], [_routine()],
    )
    occurrence = occurrences["code:sample.c:fn@10"][0]
    assert occurrence.structural_role == "condition"
    assert occurrence.condition_fact_id == "condition-fact"
    assert occurrence.presence_status == "inactive"

    context = AnalysisContext(
        id="code:sample.c:fn@10", signature="fn()", file_path="sample.c",
        start_line=10, end_line=20,
    )
    context.extra["ast_statements"] = ()
    context.extra["ast_call_occurrences"] = (occurrence,)
    profile, specs = build_code_semantic_plan(_routine(), context)
    call_fact = next(
        fact for fact in profile.evidence.values()
        if fact.kind == "CALL" and fact.attributes.get("occurrence_id")
    )
    assert call_fact.attributes["structural_role"] == "condition"
    assert call_fact.attributes["presence_status"] == "inactive"
    assert all(call_fact.id not in spec.evidence_refs for spec in specs)


def test_call_projection_rejects_a_missing_resolution_outcome() -> None:
    site = _site("effect-call", 130, "check(y)")

    with pytest.raises(ValueError, match="population"):
        build_parser_call_evidence([site], [], [_routine()])


def test_call_projection_uses_parser_line_as_the_only_structure_source() -> None:
    site = _site("effect-call", 130, "check(y)")
    resolution = _resolution("effect-call", 0)
    site["line"] = 12

    occurrences = build_parser_call_evidence([site], [resolution], [_routine()])

    assert [(item.line, item.callee) for item in occurrences["code:sample.c:fn@10"]] == [
        (3, "check")
    ]


def test_call_projection_rejects_argument_source_range_population_drift() -> None:
    site = _site("effect-call", 130, "check(y)")
    site["argument_ranges"] = [{"charOffset": 136, "charLength": 1}]
    resolution = _resolution("effect-call", 0)

    with pytest.raises(ValueError, match="source/range population mismatch"):
        build_parser_call_evidence([site], [resolution], [_routine()])


def test_branch_inventory_uses_role_not_callee_name_for_same_line_calls() -> None:
    occurrences = (
        CallOccurrenceFact(
            call_id="condition-call",
            line=1,
            callee="check",
            call_source="check(x)",
            structural_role="condition",
            condition_fact_id="condition-fact",
        ),
        CallOccurrenceFact(
            call_id="effect-call",
            line=1,
            callee="check",
            call_source="check(y)",
            structural_role="effect",
        ),
    )
    inventory = build_inventory(
        "if (check(x)) { check(y); }",
        (ControlSubtreeNode("if", "IF", 1, 1, expression="check(x)"),),
        "c",
        statements=(),
        call_occurrences=occurrences,
    )

    evidence = rule_evidence(inventory, (1,))
    direct = evidence["rows"]["R1"]["direct"]

    assert len(direct) == 1
    assert evidence["effects"][direct[0]]["occurrence_id"] == "effect-call"


def test_semantic_frame_preserves_occurrences_and_assigns_only_effect_call() -> None:
    sites = (
        _site("condition-call", 105, "check(x)"),
        _site("effect-call", 130, "check(y)"),
    )
    resolutions = (
        _resolution("condition-call", 0),
        _resolution("effect-call", 1),
    )
    occurrences = build_parser_call_evidence(
        list(sites), list(resolutions), [_routine()],
    )
    context = AnalysisContext(
        id="code:sample.c:fn@10",
        signature="fn()",
        file_path="sample.c",
        start_line=10,
        end_line=20,
    )
    context.extra["ast_statements"] = ()
    context.extra["ast_call_occurrences"] = tuple(
        occurrences["code:sample.c:fn@10"]
    )

    inventory = build_inventory(
        "if (check(x)) { check(y); }",
        (ControlSubtreeNode("if", "IF", 1, 1, expression="check(x)"),),
        "c",
        line_offset=9,
        statements=(),
        call_occurrences=context.extra["ast_call_occurrences"],
    )
    semantic_routine = _routine()
    profile, specs = build_code_semantic_plan(
        semantic_routine,
        context,
        inventory=inventory,
        rule_rows=tuple(int(row["line"]) for row in inventory.table_rows()),
    )

    call_facts = [fact for fact in profile.evidence.values() if fact.kind == "CALL"]
    occurrence_facts = [
        fact for fact in call_facts if fact.attributes.get("occurrence_id")
    ]
    processing_refs = {
        ref
        for spec in specs
        if spec.role == "PROCESSING_EFFECTS"
        for ref in spec.evidence_refs
        if ref in profile.evidence and profile.evidence[ref].kind == "CALL"
    }
    guard_refs = {
        ref
        for spec in specs
        if spec.role == "GUARDS_AND_BRANCHES"
        for ref in spec.evidence_refs
        if ref in profile.evidence and profile.evidence[ref].kind == "CALL"
    }
    assert len(occurrence_facts) == 2
    assert {fact.attributes["occurrence_id"] for fact in occurrence_facts} == {
        "condition-call", "effect-call",
    }
    assert {
        profile.evidence[ref].attributes["occurrence_id"]
        for ref in processing_refs
    } == {"effect-call"}
    assert {
        profile.evidence[ref].attributes["occurrence_id"]
        for ref in guard_refs
    } == {"condition-call"}


def test_same_line_call_effects_follow_source_range_not_hash_id() -> None:
    occurrences = (
        CallOccurrenceFact(
            call_id="z-first",
            line=1,
            callee="copy",
            call_source="copy(dst0, src0)",
            argument_sources=("dst0", "src0"),
            source_range=(10, 16),
            structural_role="effect",
        ),
        CallOccurrenceFact(
            call_id="a-second",
            line=1,
            callee="copy",
            call_source="copy(dst1, src1)",
            argument_sources=("dst1", "src1"),
            source_range=(30, 16),
            structural_role="effect",
        ),
    )
    inventory = build_inventory(
        "copy(dst0, src0); copy(dst1, src1);",
        (),
        "c",
        statements=(),
        call_occurrences=occurrences,
    )

    evidence = rule_evidence(inventory, (1,))
    refs = evidence["rows"]["R1"]["direct"]

    assert [evidence["effects"][ref]["occurrence_id"] for ref in refs] == [
        "z-first", "a-second",
    ]


def test_occurrence_call_leaf_preserves_receiver_and_ordered_arguments() -> None:
    rendered = render_structural_effect(StructuralEffect(
        op="call",
        target="handler",
        value=None,
        from_source=None,
        occurrence_id="call-1",
        call_source="ctx->handler(a, b)",
        argument_sources=("a", "b"),
        receiver_source="ctx",
    ))

    assert rendered == 'call("handler", F("ctx"), [F("a"), F("b")])'
    parsed = parse_effect(rendered)
    assert parsed.args[0].value == "handler"
