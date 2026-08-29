"""Project validated Strategy-B Rule and Example products without audit metadata."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.schema.graph_edges import HasExample, HasRule
from graph.schema.graph_nodes import CodeNode, Example, Rule
from pipeline.languages import ROUTINE_LABELS


class RuleExamplesProjectionError(ValueError):
    """Rule/Example identities or ownership are incomplete or contradictory."""


RULE_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
    "anchor_line",
    "structural_condition",
    "structural_effects",
    "business_effects",
})
EXAMPLE_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
    "given_conditions",
    "expected_effects",
})
OWNERSHIP_RELATIONSHIP_PROPERTY_KEYS = frozenset({
    "analysis_scope_id",
    "generation_id",
    "ordinal",
})


def _unique_by_id(items: Iterable[object], *, kind: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        stable_id = str(getattr(item, "id", "") or "").strip()
        if not stable_id or stable_id in result:
            raise RuleExamplesProjectionError(
                f"{kind} IDs must be unique and non-blank: {stable_id!r}"
            )
        result[stable_id] = item
    return result


def _control(analysis_scope_id: str, generation_id: str) -> dict[str, str]:
    return {
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
    }


def _validate_ordinal_population(
    ordinals_by_owner: dict[str, list[int]],
    *,
    relation_type: str,
) -> None:
    for owner_id, ordinals in ordinals_by_owner.items():
        ordered = sorted(ordinals)
        if ordered != list(range(1, len(ordered) + 1)):
            raise RuleExamplesProjectionError(
                f"{relation_type} ordinals are not one contiguous source order: "
                f"{owner_id!r} -> {ordered!r}"
            )


def build_rule_meanings_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    code_nodes: Iterable[CodeNode],
    rules: Iterable[Rule],
    has_rule_edges: Iterable[HasRule],
) -> ProductWriteBatch:
    """Create every verified Rule and its single routine owner relationship."""
    code_by_id = _unique_by_id(code_nodes, kind="CodeNode")
    routines = {
        stable_id
        for stable_id, node in code_by_id.items()
        if getattr(node, "label", "") in ROUTINE_LABELS
    }
    rule_by_id = _unique_by_id(rules, kind="Rule")

    owner_by_rule: dict[str, HasRule] = {}
    ordinals_by_owner: dict[str, list[int]] = defaultdict(list)
    for edge in has_rule_edges:
        if edge.source_id not in routines:
            raise RuleExamplesProjectionError(
                f"HAS_RULE owner is not a verified routine: {edge.source_id!r}"
            )
        if edge.target_id not in rule_by_id:
            raise RuleExamplesProjectionError(
                f"HAS_RULE target is missing: {edge.target_id!r}"
            )
        if isinstance(edge.ordinal, bool) or edge.ordinal <= 0:
            raise RuleExamplesProjectionError(
                f"HAS_RULE ordinal must be positive: {edge!r}"
            )
        if edge.target_id in owner_by_rule:
            raise RuleExamplesProjectionError(
                f"Rule has multiple HAS_RULE owners: {edge.target_id!r}"
            )
        owner_by_rule[edge.target_id] = edge
        ordinals_by_owner[edge.source_id].append(edge.ordinal)
    if set(owner_by_rule) != set(rule_by_id):
        missing = sorted(set(rule_by_id) - set(owner_by_rule))
        raise RuleExamplesProjectionError(
            f"Rule ownership is incomplete: first_missing={missing[0] if missing else ''!r}"
        )
    _validate_ordinal_population(ordinals_by_owner, relation_type="HAS_RULE")

    control = _control(analysis_scope_id, generation_id)
    node_operations: list[NodeCreateOperation] = []
    relationship_operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for rule_id, raw_rule in sorted(rule_by_id.items()):
        rule = raw_rule
        if isinstance(rule.anchor_line, bool) or rule.anchor_line <= 0:
            raise RuleExamplesProjectionError(
                f"Rule anchor_line must be positive: {rule_id!r}"
            )
        if "\n" in rule.structural_condition:
            raise RuleExamplesProjectionError(
                f"Rule condition must be one restricted-Python expression: {rule_id!r}"
            )
        if any("\n" in item or not item.strip() for item in (
            *rule.structural_effects, *rule.business_effects,
        )):
            raise RuleExamplesProjectionError(
                f"Rule effect must be a non-blank one-line restricted-Python leaf: {rule_id!r}"
            )
        if not rule.business_effects:
            raise RuleExamplesProjectionError(
                f"Rule has no verified business effect: {rule_id!r}"
            )
        owner = owner_by_rule[rule_id]
        expected_id = f"{owner.source_id}::R-{rule.anchor_line}"
        if rule_id != expected_id:
            raise RuleExamplesProjectionError(
                f"Rule stable ID differs from owner and anchor: {rule_id!r}"
            )
        properties: dict[str, object] = {
            **control,
            "stable_id": rule_id,
            "anchor_line": rule.anchor_line,
            "structural_effects": tuple(rule.structural_effects),
            "business_effects": tuple(rule.business_effects),
        }
        if rule.structural_condition.strip():
            properties["structural_condition"] = rule.structural_condition
        identity = NodeIdentity("Rule", rule_id)
        node_operations.append(NodeCreateOperation.from_properties(identity, properties))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=RULE_PROPERTY_KEYS,
            present_properties=properties,
        ))

        relationship = RelationshipIdentity(
            "HAS_RULE",
            NodeIdentity("CodeNode", owner.source_id),
            identity,
        )
        relationship_properties = {**control, "ordinal": owner.ordinal}
        relationship_operations.append(
            RelationshipCreateOperation.from_properties(
                relationship, relationship_properties,
            )
        )
        expectations.append(ProductRecordExpectation.from_properties(
            identity=relationship,
            closed_property_keys=OWNERSHIP_RELATIONSHIP_PROPERTY_KEYS,
            present_properties=relationship_properties,
        ))

    return ProductWriteBatch.build(
        stage_name="rule_meanings",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=node_operations,
        relationship_create_operations=relationship_operations,
        expectations=expectations,
    )


def build_examples_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    rules: Iterable[Rule],
    examples: Iterable[Example],
    has_example_edges: Iterable[HasExample],
) -> ProductWriteBatch:
    """Create deterministic Rule examples and their single owner relationships."""
    rule_by_id = _unique_by_id(rules, kind="Rule")
    example_by_id = _unique_by_id(examples, kind="Example")
    owner_by_example: dict[str, HasExample] = {}
    ordinals_by_owner: dict[str, list[int]] = defaultdict(list)
    for edge in has_example_edges:
        if edge.source_id not in rule_by_id:
            raise RuleExamplesProjectionError(
                f"HAS_EXAMPLE owner Rule is missing: {edge.source_id!r}"
            )
        if edge.target_id not in example_by_id:
            raise RuleExamplesProjectionError(
                f"HAS_EXAMPLE target is missing: {edge.target_id!r}"
            )
        if isinstance(edge.ordinal, bool) or edge.ordinal <= 0:
            raise RuleExamplesProjectionError(
                f"HAS_EXAMPLE ordinal must be positive: {edge!r}"
            )
        if edge.target_id in owner_by_example:
            raise RuleExamplesProjectionError(
                f"Example has multiple HAS_EXAMPLE owners: {edge.target_id!r}"
            )
        owner_by_example[edge.target_id] = edge
        ordinals_by_owner[edge.source_id].append(edge.ordinal)
    if set(owner_by_example) != set(example_by_id):
        missing = sorted(set(example_by_id) - set(owner_by_example))
        raise RuleExamplesProjectionError(
            "Example ownership is incomplete: "
            f"first_missing={missing[0] if missing else ''!r}"
        )
    _validate_ordinal_population(ordinals_by_owner, relation_type="HAS_EXAMPLE")

    control = _control(analysis_scope_id, generation_id)
    node_operations: list[NodeCreateOperation] = []
    relationship_operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for example_id, raw_example in sorted(example_by_id.items()):
        example = raw_example
        leaves = (*example.given_conditions, *example.expected_effects)
        if not leaves or any("\n" in item or not item.strip() for item in leaves):
            raise RuleExamplesProjectionError(
                f"Example must contain non-blank one-line Python leaves: {example_id!r}"
            )
        owner = owner_by_example[example_id]
        expected_id = f"{owner.source_id}::E{owner.ordinal}"
        if example_id != expected_id:
            raise RuleExamplesProjectionError(
                f"Example stable ID differs from owner and ordinal: {example_id!r}"
            )
        properties = {
            **control,
            "stable_id": example_id,
            "given_conditions": tuple(example.given_conditions),
            "expected_effects": tuple(example.expected_effects),
        }
        identity = NodeIdentity("Example", example_id)
        node_operations.append(NodeCreateOperation.from_properties(identity, properties))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=EXAMPLE_PROPERTY_KEYS,
            present_properties=properties,
        ))

        relationship = RelationshipIdentity(
            "HAS_EXAMPLE",
            NodeIdentity("Rule", owner.source_id),
            identity,
        )
        relationship_properties = {**control, "ordinal": owner.ordinal}
        relationship_operations.append(
            RelationshipCreateOperation.from_properties(
                relationship, relationship_properties,
            )
        )
        expectations.append(ProductRecordExpectation.from_properties(
            identity=relationship,
            closed_property_keys=OWNERSHIP_RELATIONSHIP_PROPERTY_KEYS,
            present_properties=relationship_properties,
        ))

    return ProductWriteBatch.build(
        stage_name="examples",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=node_operations,
        relationship_create_operations=relationship_operations,
        expectations=expectations,
    )
