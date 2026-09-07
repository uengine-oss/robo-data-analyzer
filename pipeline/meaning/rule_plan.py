"""Parser-owned branch rows를 semantic frame의 Rule evidence로 투영."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from graph.schema.graph_nodes import CodeNode
from pipeline.checks.branch_inventory import BranchInventory
from pipeline.checks.rule_condition_rendering import render_projected_row_condition
from pipeline.checks.rule_prompt_evidence import rule_evidence
from pipeline.meaning.semantic_facts import add_fact, make_fact
from pipeline.models.rule_condition_ir import attach_rule_condition_projection
from pipeline.models.semantic_frame import EvidenceFact


def append_rule_facts(
    *,
    fn: CodeNode,
    inventory: BranchInventory,
    rule_rows: Iterable[int] | None,
    rule_owner_id: str | None,
    evidence: dict[str, EvidenceFact],
    ordered: list[str],
    statement_refs: list[tuple[object, str, int, int]],
    call_refs_by_line: dict[int, list[str]],
    data_refs_by_line: dict[int, list[str]],
    expression_refs_by_node: dict[str, list[str]],
    condition_call_refs_by_fact: dict[str, list[str]],
) -> None:
    """Append exact Rule rows without parsing source or inferring meaning."""
    allowed_rows = {int(line) for line in rule_rows} if rule_rows is not None else None
    planned_rows = [
        row for row in inventory.table_rows()
        if allowed_rows is None or int(row["line"]) in allowed_rows
    ]
    canonical = attach_rule_condition_projection(
        rule_evidence(inventory, [int(row["line"]) for row in planned_rows]),
        fn.extra.get("_resolved_rule_condition_bundle"),
    )
    canonical_effects = canonical["effects"]
    canonical_rows = canonical["rows"]
    owner_id = rule_owner_id or fn.id

    for row in planned_rows:
        line = int(row["line"])
        role = str(row.get("role") or "")
        branch = inventory.at_line(line)
        condition = str(row.get("condition") or "")
        canonical_row = canonical_rows[f"R{line}"]
        projected_condition = render_projected_row_condition(canonical_row, canonical)
        effects = [
            dict(canonical_effects[ref])
            for ref in tuple(canonical_row.get("direct") or ())
        ]
        owned_lines = set(inventory.direct_evidence_lines(line))
        owned_statement_refs = tuple(
            item for item in statement_refs if item[2] in owned_lines
        )
        direct_refs = tuple(
            ref for _statement, ref, _start, _end in owned_statement_refs
        ) + tuple(
            ref
            for owned_line in sorted(owned_lines)
            for ref in call_refs_by_line.get(owned_line, ())
        ) + tuple(
            ref
            for owned_line in sorted(owned_lines)
            for ref in data_refs_by_line.get(owned_line, ())
        )
        direct_expression_refs = tuple(
            expression_ref
            for evidence_ref in direct_refs
            for expression_ref in expression_refs_by_node.get(
                str(evidence[evidence_ref].attributes.get("node_id") or ""), (),
            )
        )

        nested_control_refs: tuple[str, ...] = ()
        if branch is not None and row.get("nested_control"):
            nested_control_refs = tuple(
                add_fact(evidence, make_fact(
                    target_id=fn.id,
                    kind="CONTROL",
                    identity={
                        "nested_child": child.node_id,
                        "parent": branch.node_id,
                    },
                    owner_id=owner_id,
                    file=fn.file_path,
                    start_line=child.start + inventory.line_offset,
                    end_line=child.end + inventory.line_offset,
                    attributes={
                        "node_id": child.node_id,
                        "type": child.ntype,
                        "role": "nested_child",
                        "condition": child.condition,
                        "condition_timing": child.condition_timing,
                        "parent_ref": branch.node_id,
                        "complement_of": None,
                        "implicit_false": False,
                    },
                ))
                for child in branch.children
            )
            ordered.extend(nested_control_refs)

        branch_expression_refs: tuple[str, ...] = ()
        if branch is not None:
            own_refs = tuple(expression_refs_by_node.get(branch.node_id, ()))
            owns_case_value = any(
                evidence[ref].attributes.get("role") == "case_value"
                for ref in own_refs
            )
            parent_condition_refs = (
                tuple(
                    ref
                    for ref in expression_refs_by_node.get(branch.parent.node_id, ())
                    if evidence[ref].attributes.get("role") == "condition"
                )
                if owns_case_value and branch.parent is not None else ()
            )
            branch_expression_refs = tuple(dict.fromkeys((
                *parent_condition_refs, *own_refs,
            )))
        branch_condition_call_refs = tuple(
            call_ref
            for expression_ref in branch_expression_refs
            for call_ref in condition_call_refs_by_fact.get(
                str(evidence[expression_ref].attributes.get("fact_id") or ""), (),
            )
        )
        supporting_refs = (
            *direct_refs,
            *direct_expression_refs,
            *branch_expression_refs,
            *branch_condition_call_refs,
            *nested_control_refs,
        )
        ref = add_fact(evidence, make_fact(
            target_id=fn.id,
            kind="RULE",
            identity={"line": line, "role": role, "type": row.get("ntype")},
            owner_id=owner_id,
            file=fn.file_path,
            start_line=line,
            end_line=int(row.get("end_line") or line),
            attributes={
                "anchor_line": line,
                "role": role,
                "condition": projected_condition or "",
                "effects": effects,
                "when": list(canonical_row.get("when") or ()),
                "source_refs": list(dict.fromkeys(supporting_refs)),
                "type": str(row.get("ntype") or ""),
                "condition_timing": str(row.get("condition_timing") or ""),
                "complement_of": row.get("complement_of"),
                "implicit_false": bool("implicit_false" in condition),
                "nested_control": bool(row.get("nested_control")),
                "fallthrough_to": row.get("fallthrough_to"),
                "at_label": str(row.get("at_label") or ""),
                "after_control": str(row.get("after_control") or ""),
            },
        ))
        ordered.append(ref)
