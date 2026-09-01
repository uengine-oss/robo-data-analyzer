"""Build Rule nodes and their product relationships from verified results."""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.schema.graph_edges import HasRule, ReadsWrites
from graph.schema.graph_nodes import CodeNode, Rule


@dataclass
class RuleGraphResult:
    """Rule nodes, routine ownership, and direct table access."""

    rules: list[Rule] = field(default_factory=list)
    has_rule: list[HasRule] = field(default_factory=list)
    table_edges: list[ReadsWrites] = field(default_factory=list)


def build_rule_graph(code_nodes: list[CodeNode]) -> RuleGraphResult:
    """Project each verified routine Rule without creating duplicate product nodes."""
    result = RuleGraphResult()
    for function in code_nodes:
        _collect_rules(function.id, (function.extra or {}).get("rules") or [], result)
    return result


def _collect_rules(
    function_id: str,
    rules: list,
    result: RuleGraphResult,
) -> None:
    allowed_fields = {
        "anchor_line",
        "structural_condition",
        "structural_effects",
        "condition_description",
        "effect_descriptions",
        "table_accesses",
    }
    for order, raw_rule in enumerate(rules, start=1):
        if not isinstance(raw_rule, dict):
            raise TypeError(
                "Rule transport entry must be an object for "
                f"{function_id}: {type(raw_rule).__name__}"
            )
        unexpected = set(raw_rule) - allowed_fields
        if unexpected:
            raise ValueError(
                f"unknown Rule transport fields for {function_id}: {sorted(unexpected)}"
            )
        _collect_rule(function_id, order, raw_rule, result)


def _collect_rule(
    function_id: str,
    order: int,
    raw_rule: dict,
    result: RuleGraphResult,
) -> None:
    anchor_line = raw_rule.get("anchor_line")
    if isinstance(anchor_line, bool) or not isinstance(anchor_line, int) or anchor_line <= 0:
        raise ValueError(f"Rule requires a positive anchor_line: {function_id}")

    rule_id = f"{function_id}::R-{anchor_line}"
    effect_descriptions = tuple(
        str(value).strip()
        for value in (raw_rule.get("effect_descriptions") or ())
        if str(value).strip()
    )
    if not effect_descriptions:
        raise ValueError(
            f"product Rule requires at least one verified effect description: {rule_id}"
        )
    result.rules.append(Rule(
        id=rule_id,
        anchor_line=anchor_line,
        structural_condition=str(raw_rule.get("structural_condition") or ""),
        structural_effects=tuple(
            str(value) for value in (raw_rule.get("structural_effects") or ())
        ),
        condition_description=str(raw_rule.get("condition_description") or "").strip(),
        effect_descriptions=effect_descriptions,
    ))
    result.has_rule.append(HasRule(
        source_id=function_id,
        target_id=rule_id,
        ordinal=order,
    ))
    result.table_edges.extend(
        _table_edges(rule_id, raw_rule.get("table_accesses") or ())
    )


def _table_edges(rule_id: str, values: list) -> list[ReadsWrites]:
    operations: dict[tuple[str, str], set[str]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Rule table access must be an object")
        table_id = str(value.get("table") or "").strip()
        access = str(value.get("access") or "").strip().upper()
        operation = str(value.get("operation") or "").strip().upper()
        if (
            not table_id
            or access not in {"READ", "WRITE", "READ_WRITE"}
            or operation not in {
                "READ", "INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE",
            }
        ):
            raise ValueError(
                f"invalid Rule table access for {rule_id}: "
                f"table={table_id!r}, access={access!r}, operation={operation!r}"
            )
        relations = (
            ("READS", "WRITES") if access == "READ_WRITE"
            else ("READS",) if access == "READ"
            else ("WRITES",)
        )
        for relation in relations:
            operations.setdefault((table_id, relation), set()).add(operation)
    return [
        ReadsWrites(
            source_id=rule_id,
            target_id=table_id,
            relation=relation,
            operations=tuple(sorted(names)),
        )
        for (table_id, relation), names in sorted(operations.items())
    ]
