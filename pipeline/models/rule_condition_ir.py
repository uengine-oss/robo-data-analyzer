"""Deterministic Rule-condition projection from Analyzer expression evidence.

The projection keeps exact source fragments inside the established ``F(...)`` leaf
contract and uses only Python operator shells proven by discriminated expression IR.
It never reparses source text or infers binding, type, linkage, or runtime values.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


RULE_CONDITION_EXPRESSION_SCHEMA = "rule-condition-expression/v1"


class RuleConditionProjectionError(ValueError):
    """Rule condition expression evidence is incomplete or internally inconsistent."""


_COMPARISONS = {
    "equal": "==",
    "not_equal": "!=",
    "less": "<",
    "less_equal": "<=",
    "greater": ">",
    "greater_equal": ">=",
}
_LOGICAL = {"logical_and": "and", "logical_or": "or"}


def _mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuleConditionProjectionError(f"{name} must be an object")
    return value


def _source_range(value: object, *, name: str) -> tuple[int, int]:
    source_range = _mapping(value, name=name)
    offset = int(source_range.get("char_offset") or 0)
    length = int(source_range.get("char_length") or 0)
    if offset < 0 or length <= 0:
        raise RuleConditionProjectionError(f"{name} must be a positive exact range")
    return offset, length


def _expression_fragment(
    fact: Mapping[str, Any],
    expression: Mapping[str, Any] | None = None,
) -> str:
    root_offset, root_length = _source_range(
        fact.get("source_range"), name="expression fact source_range",
    )
    source_text = str(fact.get("source_text") or "")
    if len(source_text) != root_length:
        raise RuleConditionProjectionError(
            "expression source text does not match its exact root range"
        )
    node = expression or _mapping(
        fact.get("expression"), name="expression fact value",
    )
    offset, length = _source_range(
        node.get("source_range"), name="expression node source_range",
    )
    relative = offset - root_offset
    if relative < 0 or relative + length > len(source_text):
        raise RuleConditionProjectionError(
            "expression node range escapes its exact source text"
        )
    fragment = source_text[relative:relative + length]
    if not fragment:
        raise RuleConditionProjectionError("expression source fragment is empty")
    return fragment


def _source_atom(
    fact: Mapping[str, Any],
    expression: Mapping[str, Any] | None = None,
) -> str:
    return "F(" + json.dumps(
        _expression_fragment(fact, expression), ensure_ascii=False,
    ) + ")"


def _child(expression: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _mapping(expression.get(name), name=f"expression {name}")


def _children(expression: Mapping[str, Any], name: str) -> tuple[Mapping[str, Any], ...]:
    raw = expression.get(name)
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise RuleConditionProjectionError(f"expression {name} must be an object array")
    return tuple(raw)


def _render_predicate(
    fact: Mapping[str, Any],
    expression: Mapping[str, Any],
) -> str:
    kind = str(expression.get("kind") or "")
    if kind == "group":
        return f"({_render_predicate(fact, _child(expression, 'value'))})"
    if kind == "unary" and expression.get("operator") == "logical_not":
        return f"not ({_render_predicate(fact, _child(expression, 'operand'))})"
    if kind == "binary":
        operator = str(expression.get("operator") or "")
        operands = _children(expression, "operands")
        logical = _LOGICAL.get(operator)
        if logical is not None and len(operands) >= 2:
            return f" {logical} ".join(
                f"({_render_predicate(fact, operand)})" for operand in operands
            )
        comparison = _COMPARISONS.get(operator)
        # C-family repeated comparisons are left-associative, while Python chains
        # comparisons.  Preserve the exact source atom instead of changing meaning.
        if comparison is not None and len(operands) == 2:
            return (
                f"{_source_atom(fact, operands[0])} {comparison} "
                f"{_source_atom(fact, operands[1])}"
            )
    return _source_atom(fact, expression)


def render_condition_relation(
    relation: Mapping[str, Any],
    expressions: Mapping[str, Mapping[str, Any]],
) -> str:
    """Render one closed condition relation without reading or parsing source."""
    kind = str(relation.get("kind") or "")
    refs = relation.get("expression_refs")
    if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
        raise RuleConditionProjectionError(
            "condition relation requires ordered expression_refs"
        )
    if len(refs) != len(set(refs)):
        raise RuleConditionProjectionError("condition relation expression_refs repeat")
    try:
        facts = tuple(expressions[ref] for ref in refs)
    except KeyError as error:
        raise RuleConditionProjectionError(
            f"condition relation expression is missing: {error.args[0]}"
        ) from error
    if kind == "predicate" and len(facts) == 1:
        expression = _mapping(facts[0].get("expression"), name="predicate expression")
        return _render_predicate(facts[0], expression)
    if kind == "case_match" and len(facts) == 2:
        return f"{_source_atom(facts[0])} == {_source_atom(facts[1])}"
    raise RuleConditionProjectionError(
        f"unsupported condition relation shape: kind={kind!r}, refs={len(facts)}"
    )


def attach_rule_condition_projection(
    evidence: Mapping[str, object],
    bundle: Mapping[str, object] | None,
) -> dict[str, object]:
    """Attach only the expression closure referenced by one Rule evidence packet."""
    result = dict(evidence)
    if bundle is None:
        return result
    if bundle.get("schema") != RULE_CONDITION_EXPRESSION_SCHEMA:
        raise RuleConditionProjectionError("unsupported Rule condition bundle schema")
    raw_conditions = _mapping(evidence.get("conditions"), name="Rule conditions")
    relations = _mapping(bundle.get("conditions"), name="condition relations")
    expressions = _mapping(bundle.get("expressions"), name="condition expressions")
    condition_keys = tuple(str(ref) for ref in raw_conditions)
    missing = set(condition_keys) - set(relations)
    if missing:
        raise RuleConditionProjectionError(
            f"Rule condition expression closure is incomplete: {sorted(missing)}"
        )
    selected_relations = {
        ref: dict(_mapping(relations[ref], name=f"condition relation {ref}"))
        for ref in condition_keys
    }
    expression_refs = list(dict.fromkeys(
        expression_ref
        for relation in selected_relations.values()
        for expression_ref in relation.get("expression_refs") or ()
    ))
    missing_expressions = set(expression_refs) - set(expressions)
    if missing_expressions:
        raise RuleConditionProjectionError(
            "Rule condition expression facts are missing: "
            f"{sorted(missing_expressions)}"
        )
    selected_expressions = {
        ref: dict(_mapping(expressions[ref], name=f"condition expression {ref}"))
        for ref in expression_refs
    }
    # Render now as a validation gate; materialization repeats the same pure function.
    for ref, relation in selected_relations.items():
        if not render_condition_relation(relation, selected_expressions):
            raise RuleConditionProjectionError(f"empty projected condition: {ref}")
    result["condition_expression_schema"] = RULE_CONDITION_EXPRESSION_SCHEMA
    result["condition_relations"] = selected_relations
    result["condition_expressions"] = selected_expressions
    return result
