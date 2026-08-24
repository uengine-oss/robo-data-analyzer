"""Build routine-local Rule condition relations from resolved expression facts."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import ROUTINE_LABELS
from pipeline.models.rule_condition_ir import RULE_CONDITION_EXPRESSION_SCHEMA


class RuleConditionOwnershipError(ValueError):
    """A condition fact cannot be related to its structural Rule owner exactly once."""


def _routine_owner(
    node: CodeNode,
    code_by_id: Mapping[str, CodeNode],
) -> CodeNode:
    current = node
    seen: set[str] = set()
    while current.label not in ROUTINE_LABELS:
        if current.id in seen:
            raise RuleConditionOwnershipError(
                f"Rule condition owner parent cycle at {current.id!r}"
            )
        seen.add(current.id)
        parent = code_by_id.get(current.parent_id or "")
        if parent is None:
            raise RuleConditionOwnershipError(
                f"Rule condition owner has no routine parent: {node.id!r}"
            )
        current = parent
    return current


def build_routine_rule_condition_bundles(
    code_nodes: Sequence[CodeNode],
    resolved_facts: Sequence[dict],
    structural_facts: Sequence[dict],
) -> dict[str, dict[str, object]]:
    """Return exact predicate/case relations grouped by nearest routine owner."""
    code_by_id = {node.id: node for node in code_nodes}
    if len(code_by_id) != len(code_nodes):
        raise RuleConditionOwnershipError(
            "Rule condition projection requires unique CodeNode IDs"
        )
    source_by_fact: dict[str, str] = {}
    for fact in structural_facts:
        fact_id = str(fact.get("fact_id") or "")
        source_text = str(fact.get("expression_source") or "")
        if not fact_id or not source_text or fact_id in source_by_fact:
            raise RuleConditionOwnershipError(
                "structural condition sources require unique fact IDs and exact text"
            )
        source_by_fact[fact_id] = source_text

    relevant: dict[str, dict] = {}
    facts_by_node: dict[str, list[dict]] = {}
    for raw in resolved_facts:
        role = str(raw.get("role") or "")
        if role not in {"condition", "case_value"}:
            continue
        fact_id = str(raw.get("fact_id") or "")
        node_id = str(raw.get("code_node_id") or "")
        if not fact_id or fact_id in relevant or node_id not in code_by_id:
            raise RuleConditionOwnershipError(
                f"resolved Rule condition fact is invalid: {fact_id!r}"
            )
        source_text = source_by_fact.get(fact_id)
        if source_text is None:
            raise RuleConditionOwnershipError(
                f"resolved Rule condition source is missing: {fact_id!r}"
            )
        fact = {**dict(raw), "source_text": source_text}
        relevant[fact_id] = fact
        facts_by_node.setdefault(node_id, []).append(fact)

    bundles: dict[str, dict[str, object]] = {}

    def bundle_for(routine: CodeNode) -> dict[str, object]:
        return bundles.setdefault(routine.id, {
            "schema": RULE_CONDITION_EXPRESSION_SCHEMA,
            "conditions": {},
            "expressions": {},
        })

    for fact_id, fact in relevant.items():
        node = code_by_id[str(fact["code_node_id"])]
        routine = _routine_owner(node, code_by_id)
        bundle = bundle_for(routine)
        conditions = bundle["conditions"]
        expressions = bundle["expressions"]
        if not isinstance(conditions, dict) or not isinstance(expressions, dict):
            raise RuleConditionOwnershipError("invalid internal condition bundle")
        condition_ref = f"C{int(fact.get('start_line') or 0)}"
        if condition_ref == "C0" or condition_ref in conditions:
            raise RuleConditionOwnershipError(
                f"Rule condition coordinate is not unique: {condition_ref}"
            )
        if fact["role"] == "condition":
            relation = {"kind": "predicate", "expression_refs": [fact_id]}
            referenced = (fact,)
        else:
            parent = code_by_id.get(node.parent_id or "")
            parent_conditions = (
                tuple(
                    candidate for candidate in facts_by_node.get(parent.id, ())
                    if candidate.get("role") == "condition"
                )
                if parent is not None else ()
            )
            if len(parent_conditions) != 1:
                raise RuleConditionOwnershipError(
                    f"case parent condition must be exact-one: {fact_id!r}"
                )
            subject = parent_conditions[0]
            if _routine_owner(parent, code_by_id).id != routine.id:
                raise RuleConditionOwnershipError(
                    f"case parent condition crosses routine ownership: {fact_id!r}"
                )
            relation = {
                "kind": "case_match",
                "expression_refs": [subject["fact_id"], fact_id],
            }
            referenced = (subject, fact)
        conditions[condition_ref] = relation
        for referenced_fact in referenced:
            expressions.setdefault(
                str(referenced_fact["fact_id"]), dict(referenced_fact),
            )
    return bundles


def attach_routine_rule_condition_bundles(
    code_nodes: Sequence[CodeNode],
    resolved_facts: Sequence[dict],
    structural_facts: Sequence[dict],
) -> dict[str, dict[str, object]]:
    """Attach Analyzer-internal condition relations without graph persistence."""
    bundles = build_routine_rule_condition_bundles(
        code_nodes, resolved_facts, structural_facts,
    )
    by_id = {node.id: node for node in code_nodes}
    for routine_id, bundle in bundles.items():
        routine = by_id[routine_id]
        if "_resolved_rule_condition_bundle" in routine.extra:
            raise RuleConditionOwnershipError(
                f"routine Rule condition bundle already attached: {routine_id!r}"
            )
        routine.extra["_resolved_rule_condition_bundle"] = bundle
    return bundles
