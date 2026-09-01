from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.models.rule_condition_ir import attach_rule_condition_projection
from pipeline.models.rule_materialization import materialize_rule_items
from pipeline.parsing.resolution.rule_condition_projection import (
    attach_routine_rule_condition_bundles,
    build_routine_rule_condition_bundles,
)


def _node(
    node_id: str,
    label: str,
    line: int,
    *,
    parent_id: str | None = None,
) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path="sample.c",
        start_line=line,
        end_line=line + 1,
        label=label,
        name=node_id,
        parent_id=parent_id,
    )


def _reference_fact(
    fact_id: str,
    node_id: str,
    role: str,
    line: int,
    offset: int,
    text: str,
) -> dict:
    source_range = {"char_offset": offset, "char_length": len(text)}
    return {
        "fact_id": fact_id,
        "source_id": "sample.c",
        "code_node_id": node_id,
        "role": role,
        "start_line": line,
        "end_line": line,
        "source_range": source_range,
        "expression": {
            "kind": "reference",
            "source_range": source_range,
            "name": text,
        },
    }


def _structural_source(fact_id: str, text: str) -> dict:
    return {"fact_id": fact_id, "expression_source": text}


def _case_bundle() -> dict:
    routine = _node("routine", "FUNCTION", 1)
    switch = _node("switch", "SWITCH", 2, parent_id=routine.id)
    case = _node("case", "CASE", 3, parent_id=switch.id)
    subject = _reference_fact(
        "subject", switch.id, "condition", 2, 10, "rc",
    )
    value = _reference_fact(
        "value", case.id, "case_value", 3, 20, "RC_NFD",
    )
    bundles = build_routine_rule_condition_bundles(
        (routine, switch, case),
        (subject, value),
        (_structural_source("subject", "rc"), _structural_source("value", "RC_NFD")),
    )
    return bundles[routine.id]


def test_case_value_is_projected_as_controller_subject_equality() -> None:
    evidence = {
        "conditions": {"C3": "RC_NFD"},
        "effects": {},
        "rows": {"R3": {"role": "decision", "when": ["C3"]}},
    }

    projected = attach_rule_condition_projection(evidence, _case_bundle())
    rules = materialize_rule_items(projected, (3,))

    assert rules[0].structural_condition == 'F("rc") == F("RC_NFD")'


def test_case_complement_negates_the_same_canonical_relation() -> None:
    evidence = {
        "conditions": {"C3": "RC_NFD"},
        "effects": {},
        "rows": {"R4": {"role": "complement", "when": ["!C3"]}},
    }

    projected = attach_rule_condition_projection(evidence, _case_bundle())
    rules = materialize_rule_items(projected, (4,))

    assert rules[0].structural_condition == 'not (F("rc") == F("RC_NFD"))'


def test_projection_fails_closed_when_case_parent_subject_is_missing() -> None:
    routine = _node("routine", "FUNCTION", 1)
    switch = _node("switch", "SWITCH", 2, parent_id=routine.id)
    case = _node("case", "CASE", 3, parent_id=switch.id)
    value = _reference_fact(
        "value", case.id, "case_value", 3, 20, "RC_NFD",
    )

    try:
        build_routine_rule_condition_bundles(
            (routine, switch, case),
            (value,),
            (_structural_source("value", "RC_NFD"),),
        )
    except ValueError as error:
        assert "parent condition" in str(error)
    else:
        raise AssertionError("case projection accepted a missing controller subject")


def test_binary_operator_shell_keeps_exact_source_atoms() -> None:
    routine = _node("routine", "FUNCTION", 1)
    decision = _node("decision", "IF", 2, parent_id=routine.id)
    root_range = {"char_offset": 10, "char_length": 6}
    fact = {
        "fact_id": "predicate",
        "source_id": "sample.c",
        "code_node_id": decision.id,
        "role": "condition",
        "start_line": 2,
        "end_line": 2,
        "source_range": root_range,
        "expression": {
            "kind": "binary",
            "source_range": root_range,
            "operator": "equal",
            "operands": [
                {
                    "kind": "reference",
                    "source_range": {"char_offset": 10, "char_length": 1},
                    "name": "x",
                },
                {
                    "kind": "literal",
                    "source_range": {"char_offset": 15, "char_length": 1},
                    "spelling": "0",
                },
            ],
        },
    }
    bundle = build_routine_rule_condition_bundles(
        (routine, decision),
        (fact,),
        (_structural_source("predicate", "x == 0"),),
    )[routine.id]
    evidence = attach_rule_condition_projection({
        "conditions": {"C2": "x == 0"},
        "effects": {},
        "rows": {"R2": {"role": "decision", "when": ["C2"]}},
    }, bundle)

    assert materialize_rule_items(evidence, (2,))[0].structural_condition == (
        'F("x") == F("0")'
    )


def test_routine_attachment_preserves_the_exact_built_bundle() -> None:
    routine = _node("routine", "FUNCTION", 1)
    decision = _node("decision", "IF", 2, parent_id=routine.id)
    fact = _reference_fact(
        "predicate", decision.id, "condition", 2, 10, "ready",
    )

    bundles = attach_routine_rule_condition_bundles(
        (routine, decision),
        (fact,),
        (_structural_source("predicate", "ready"),),
    )

    assert routine.extra["_resolved_rule_condition_bundle"] is bundles[routine.id]
    assert tuple(bundles[routine.id]["conditions"]) == ("C2",)


def test_multiline_condition_uses_control_node_line_not_expression_line() -> None:
    routine = _node("routine", "FUNCTION", 1)
    decision = _node("decision", "IF", 10, parent_id=routine.id)
    fact = _reference_fact(
        "predicate", decision.id, "condition", 12, 40, "ready",
    )

    bundle = build_routine_rule_condition_bundles(
        (routine, decision),
        (fact,),
        (_structural_source("predicate", "ready"),),
    )[routine.id]

    assert tuple(bundle["conditions"]) == ("C10",)
    projected = attach_rule_condition_projection({
        "conditions": {"C10": "ready"},
        "effects": {},
        "rows": {"R10": {"role": "decision", "when": ["C10"]}},
    }, bundle)
    assert materialize_rule_items(projected, (10,))[0].structural_condition == (
        'F("ready")'
    )


def test_projection_rejects_an_incomplete_rule_condition_closure() -> None:
    evidence = {
        "conditions": {"C3": "RC_NFD", "C4": "RC_NRM"},
        "effects": {},
        "rows": {"R3": {"role": "decision", "when": ["C3"]}},
    }

    try:
        attach_rule_condition_projection(evidence, _case_bundle())
    except ValueError as error:
        assert "closure is incomplete" in str(error)
    else:
        raise AssertionError("partial Rule condition projection was accepted")
