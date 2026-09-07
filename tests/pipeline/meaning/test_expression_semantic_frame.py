from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import (
    AnalysisContext,
    ControlSubtreeNode,
    StatementFact,
)
from pipeline.checks.branch_inventory import build_inventory
from pipeline.checks.rule_obligations import required_rule_rows
from pipeline.meaning.semantic_frame_planner import (
    build_code_semantic_plan,
)


def _expression(
    fact_id: str,
    role: str,
    expression: dict,
    *,
    owner: str = "assign",
    line: int = 11,
) -> dict:
    return {
        "fact_id": fact_id,
        "source_id": "sample.c",
        "code_node_id": owner,
        "role": role,
        "start_line": line,
        "end_line": line,
        "source_range": {"char_offset": 40, "char_length": 1},
        "expression": expression,
    }


def test_processing_slot_keeps_expression_canonical() -> None:
    fn = CodeNode(
        id="routine",
        signature="void routine(void)",
        file_path="sample.c",
        start_line=10,
        end_line=20,
        label="FUNCTION",
        name="routine",
    )
    fn.extra["_resolved_expression_facts"] = (
        _expression("target", "assignment_target", {
            "kind": "reference",
            "source_range": {"char_offset": 40, "char_length": 1},
            "name": "x",
        }),
        _expression("value", "assignment_value", {
            "kind": "literal",
            "source_range": {"char_offset": 44, "char_length": 1},
            "spelling": "1",
        }),
    )
    context = AnalysisContext(
        id=fn.id,
        signature=fn.signature,
        file_path=fn.file_path,
        start_line=fn.start_line,
        end_line=fn.end_line,
        extra={
            "ast_statements": (
                StatementFact(
                    node_id="assign",
                    label="ASSIGNMENT",
                    start=2,
                    end=2,
                    expression="1",
                    target="x",
                    operator="=",
                ),
            ),
        },
    )

    profile, specs = build_code_semantic_plan(fn, context)

    assert len(profile.expression_refs) == 2
    assert {profile.evidence[ref].kind for ref in profile.expression_refs} == {
        "EXPRESSION",
    }
    effect = next(
        spec for spec in specs
        if spec.family == "FUNCTION" and spec.role == "WHOLE_FUNCTION"
    )
    assert set(profile.expression_refs).issubset(effect.evidence_refs)
    assert not any(
        spec.family == "FUNCTION"
        and spec.role in {"RESPONSIBILITY", "INPUT_ROLE"}
        for spec in specs
    )
    # Full nested provenance remains canonical for deterministic consumers.
    assert profile.evidence[profile.expression_refs[0]].attributes["expression"][
        "source_range"
    ] == {"char_offset": 40, "char_length": 1}


def test_case_slot_relates_parent_subject_to_case_value_by_structure() -> None:
    fn = CodeNode(
        id="routine",
        signature="int routine(void)",
        file_path="sample.c",
        start_line=1,
        end_line=6,
        label="FUNCTION",
        name="routine",
    )
    switch = CodeNode(
        id="switch",
        signature="switch",
        file_path=fn.file_path,
        start_line=2,
        end_line=5,
        label="SWITCH",
        parent_id=fn.id,
        expression="rc",
    )
    case = CodeNode(
        id="case",
        signature="case",
        file_path=fn.file_path,
        start_line=3,
        end_line=4,
        label="CASE",
        parent_id=switch.id,
        expression="RC_NRM",
    )
    fn.extra["_resolved_expression_facts"] = (
        _expression("subject", "condition", {
            "kind": "reference",
            "source_range": {"char_offset": 20, "char_length": 2},
            "name": "rc",
        }, owner=switch.id, line=2),
        _expression("case-value", "case_value", {
            "kind": "reference",
            "source_range": {"char_offset": 31, "char_length": 6},
            "name": "RC_NRM",
        }, owner=case.id, line=3),
    )
    control = ControlSubtreeNode(
        node_id=switch.id,
        ntype="SWITCH",
        start=2,
        end=5,
        expression="rc",
        children=[ControlSubtreeNode(
            node_id=case.id,
            ntype="CASE",
            start=3,
            end=4,
            expression="RC_NRM",
        )],
    )
    statement = StatementFact(
        node_id="return",
        label="RETURN",
        start=4,
        end=4,
        expression="1",
    )
    context = AnalysisContext(
        id=fn.id,
        signature=fn.signature,
        file_path=fn.file_path,
        start_line=fn.start_line,
        end_line=fn.end_line,
        extra={"ast_statements": (statement,), "control_subtree": [control]},
    )
    inventory = build_inventory(
        "int f() {\nswitch (rc) {\ncase RC_NRM:\nreturn 1;\n}\n}",
        [control],
        "c",
        statements=(statement,),
    )

    profile, specs = build_code_semantic_plan(
        fn,
        context,
        inventory=inventory,
        rule_rows=required_rule_rows(inventory),
        rule_owner_id=fn.id,
        child_nodes=(switch, case),
    )

    by_fact_id = {
        fact.attributes.get("fact_id"): ref
        for ref, fact in profile.evidence.items()
        if fact.kind == "EXPRESSION"
    }
    case_guard = next(
        spec for spec in specs
        if spec.family == "FUNCTION"
        and spec.role == "WHOLE_FUNCTION"
        and by_fact_id["case-value"] in spec.evidence_refs
    )
    assert by_fact_id["subject"] in case_guard.evidence_refs
