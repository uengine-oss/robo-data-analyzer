"""Render canonical Rule rows from optional Analyzer condition-expression evidence."""
from __future__ import annotations

import json
from collections.abc import Mapping

from pipeline.models.rule_condition_ir import (
    RULE_CONDITION_EXPRESSION_SCHEMA,
    RuleConditionProjectionError,
    render_condition_relation,
)
from pipeline.quality_gates.rule_prompt_evidence import render_row_condition


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuleConditionProjectionError(f"{name} must be an object")
    return value


def render_projected_row_condition(
    row: Mapping[str, object],
    evidence: Mapping[str, object],
) -> str | None:
    """Render one row from embedded expression evidence, or the legacy contract."""
    schema = evidence.get("condition_expression_schema")
    conditions = _mapping(evidence.get("conditions"), name="Rule conditions")
    if schema is None:
        return render_row_condition(row, conditions)
    if schema != RULE_CONDITION_EXPRESSION_SCHEMA:
        raise RuleConditionProjectionError("unsupported embedded condition schema")
    relations = _mapping(
        evidence.get("condition_relations"), name="embedded condition relations",
    )
    expressions = _mapping(
        evidence.get("condition_expressions"), name="embedded condition expressions",
    )
    clauses: list[str] = []
    for raw_term in row.get("when") or ():
        term = str(raw_term)
        holds = not term.startswith("!")
        ref = term if holds else term[1:]
        relation = _mapping(relations.get(ref), name=f"embedded relation {ref}")
        atom = render_condition_relation(relation, expressions)
        clauses.append(atom if holds else f"not ({atom})")
    after = str(row.get("after_control") or "").strip()
    if after:
        clauses.append(f"after_control({json.dumps(after, ensure_ascii=False)})")
    label = str(row.get("at_label") or "").strip()
    if label:
        clauses.append(f"at_label({json.dumps(label, ensure_ascii=False)})")
    return " and ".join(clauses) or None
