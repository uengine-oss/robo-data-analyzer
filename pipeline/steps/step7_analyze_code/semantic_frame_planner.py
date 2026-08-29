"""Project existing parser/linker facts into bounded semantic frame slots."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import (
    AnalysisContext,
    CallOccurrenceFact,
    StatementFact,
)
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.languages import callsite_labels, control_structure_labels
from pipeline.models.rule_condition_ir import attach_rule_condition_projection
from pipeline.models.semantic_frame import (
    EvidenceFact,
    FUNCTION_SUMMARY_ROLES,
    SlotSpec,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    slot_id,
)
from pipeline.quality_gates.branch_inventory import BranchInventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.quality_gates.rule_condition_rendering import (
    render_projected_row_condition,
)


_WRITE_FAMILY = {
    "INSERT": "INSERT",
    "UPDATE": "UPDATE",
    "DELETE": "DELETE",
    "TRUNCATE": "DELETE",
    "MERGE": "UPSERT",
}

_EXPRESSION_ROLES = frozenset({
    "condition", "case_value", "return_value", "assignment_target",
    "assignment_value", "initializer_value", "update_expression",
})
_EXPRESSION_KINDS = frozenset({
    "reference", "literal", "group", "member", "subscript", "call", "unary",
    "binary", "conditional", "assignment", "cast", "sequence",
})


def _fact(
    *, target_id: str, kind: str, identity: object, owner_id: str,
    file: str = "", start_line: int = 0, end_line: int = 0,
    attributes: Mapping[str, Any] | None = None,
) -> EvidenceFact:
    return EvidenceFact(
        id=evidence_id(target_id, kind, identity),
        kind=kind,
        owner_id=owner_id,
        file=file,
        start_line=start_line,
        end_line=end_line,
        attributes=dict(attributes or {}),
    )


def _add_fact(evidence: dict[str, EvidenceFact], fact: EvidenceFact) -> str:
    existing = evidence.get(fact.id)
    if existing is not None and existing != fact:
        raise ValueError(f"semantic evidence id collision: {fact.id}")
    evidence[fact.id] = fact
    return fact.id


def _add_slot(
    slots: dict[str, SlotSpec], *, target_id: str, family: str, role: str,
    anchor: object, target_ref: str, evidence_refs: Iterable[str],
) -> None:
    refs = tuple(dict.fromkeys(str(ref) for ref in evidence_refs if str(ref)))
    if not refs:
        raise ValueError(f"semantic slot requires evidence: {family}.{role}")
    sid = slot_id(target_id, family, role, anchor)
    spec = SlotSpec(
        slot_id=sid,
        family=family,
        role=role,
        target_ref=target_ref,
        evidence_refs=refs,
    )
    existing = slots.get(sid)
    if existing is not None and existing != spec:
        raise ValueError(f"semantic slot id collision: {sid}")
    slots[sid] = spec


def build_code_semantic_plan(
    fn: CodeNode,
    ctx: AnalysisContext,
    *,
    inventory: BranchInventory | None = None,
    rule_rows: Iterable[int] | None = None,
    rule_owner_id: str | None = None,
    child_nodes: Iterable[CodeNode] = (),
    unsupported: Iterable[Mapping[str, Any]] = (),
) -> tuple[StructuralProfile, tuple[SlotSpec, ...]]:
    """Build a common Framework/DBMS profile and its applicable shallow slots.

    The function consumes only facts already present in CodeNode/AnalysisContext and the
    shared BranchInventory.  It does not parse source text or infer business meaning.
    """
    target = StructuralTarget(
        id=fn.id,
        type=fn.label,
        owner_id=fn.owner_id,
        file=fn.file_path,
        start_line=fn.start_line,
        end_line=fn.end_line,
    )
    owner_id = fn.id
    evidence: dict[str, EvidenceFact] = {}
    slots: dict[str, SlotSpec] = {}
    ordered: list[str] = []
    child_refs: list[str] = []
    callee_refs: list[str] = []
    linked_callee_refs: list[str] = []
    unavailable_callee_refs: list[str] = []
    external_refs: list[str] = []
    data_refs: list[str] = []
    symbol_refs: list[str] = []
    expression_refs: list[str] = []
    expression_refs_by_node: dict[str, list[str]] = {}
    parameter_refs: list[str] = []
    planned_rows: list[dict[str, Any]] = []
    planned_rule_refs: list[tuple[dict[str, Any], str, tuple[str, ...]]] = []

    target_ref = _add_fact(evidence, _fact(
        target_id=fn.id,
        kind="TARGET",
        identity=0,
        owner_id=owner_id,
        file=fn.file_path,
        start_line=fn.start_line,
        end_line=fn.end_line,
        attributes={
            "type": fn.label,
            "signature": fn.signature,
            "name": fn.name,
            "params": fn.params,
            "return_type": fn.return_type,
        },
    ))

    raw_expression_facts = fn.extra.get("_resolved_expression_facts")
    if raw_expression_facts is not None and not isinstance(
        raw_expression_facts, (tuple, list),
    ):
        raise TypeError("routine resolved expression facts must be a sequence")
    seen_expression_ids: set[str] = set()
    for record in raw_expression_facts or ():
        if not isinstance(record, Mapping):
            raise TypeError(
                f"unexpected resolved expression fact: {type(record).__name__}"
            )
        fact_id = str(record.get("fact_id") or "")
        code_node_id = str(record.get("code_node_id") or "")
        role = str(record.get("role") or "")
        start_line = int(record.get("start_line") or 0)
        end_line = int(record.get("end_line") or 0)
        source_range = record.get("source_range")
        expression = record.get("expression")
        if not fact_id or fact_id in seen_expression_ids:
            raise ValueError("routine expression fact IDs must be non-empty and unique")
        if not code_node_id or role not in _EXPRESSION_ROLES:
            raise ValueError(
                f"invalid routine expression ownership/role: {fact_id!r}"
            )
        if not (
            fn.start_line <= start_line <= end_line <= fn.end_line
        ):
            raise ValueError(
                f"routine expression line range is outside target: {fact_id!r} "
                f"{start_line}-{end_line} vs {fn.start_line}-{fn.end_line}"
            )
        if not isinstance(source_range, Mapping) or not isinstance(expression, Mapping):
            raise TypeError(f"routine expression range/value is invalid: {fact_id!r}")
        if str(expression.get("kind") or "") not in _EXPRESSION_KINDS:
            raise ValueError(f"unsupported routine expression kind: {fact_id!r}")
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="EXPRESSION",
            identity={"fact_id": fact_id},
            owner_id=owner_id,
            file=fn.file_path,
            start_line=start_line,
            end_line=end_line,
            attributes={
                "fact_id": fact_id,
                "source_id": str(record.get("source_id") or ""),
                "code_node_id": code_node_id,
                "role": role,
                "source_range": dict(source_range),
                "expression": dict(expression),
            },
        ))
        expression_refs.append(ref)
        expression_refs_by_node.setdefault(code_node_id, []).append(ref)
        ordered.append(ref)
        seen_expression_ids.add(fact_id)

    control_labels = control_structure_labels()
    for child in sorted(child_nodes, key=lambda item: (item.start_line, item.end_line, item.id)):
        is_control = child.label in control_labels
        attributes = (
            {
                "node_id": child.id,
                "type": child.label,
                "role": "child",
                "condition": child.expression,
                "condition_timing": child.condition_timing,
                "parent_ref": child.parent_id or "",
                "complement_of": None,
                "implicit_false": False,
            }
            if is_control else {
                "node_id": child.id,
                "type": child.label,
                "expression": child.expression,
                "target": child.target,
                "operator": child.operator,
                "effects": [],
                "terminates": child.label in {
                    "RETURN", "THROW", "RAISE", "BREAK", "CONTINUE", "GOTO",
                    "EXIT",
                },
            }
        )
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="CONTROL" if is_control else "STATEMENT",
            identity={"child": child.id},
            owner_id=owner_id,
            file=child.file_path,
            start_line=child.start_line,
            end_line=child.end_line,
            attributes=attributes,
        ))
        child_refs.append(ref)
        ordered.append(ref)

    statements = tuple(ctx.extra.get("ast_statements") or ())
    statement_refs: list[tuple[StatementFact, str, int, int]] = []
    for statement in statements:
        absolute_start = fn.start_line + statement.start - 1
        absolute_end = fn.start_line + statement.end - 1
        effects = _statement_effects(statement)
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="STATEMENT",
            identity={"node": statement.node_id, "line": absolute_start},
            owner_id=owner_id,
            file=fn.file_path,
            start_line=absolute_start,
            end_line=absolute_end,
            attributes={
                "node_id": statement.node_id,
                "type": statement.label,
                "expression": statement.expression,
                "target": statement.target,
                "operator": statement.operator,
                "effects": effects,
                "terminates": statement.label in {
                    "RETURN", "THROW", "RAISE", "BREAK", "CONTINUE", "GOTO",
                    "EXIT",
                },
            },
        ))
        statement_refs.append((statement, ref, absolute_start, absolute_end))
        ordered.append(ref)

    call_refs_by_line: dict[int, list[str]] = {}
    condition_call_refs_by_fact: dict[str, list[str]] = {}
    effect_call_refs: list[str] = []
    call_occurrences = tuple(ctx.extra.get("ast_call_occurrences") or ())
    if call_occurrences:
        if not all(isinstance(item, CallOccurrenceFact) for item in call_occurrences):
            raise ValueError("ast_call_occurrences requires CallOccurrenceFact values")
        occurrence_ids = [item.call_id for item in call_occurrences]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("ast_call_occurrences requires unique occurrence IDs")
        for occurrence in call_occurrences:
            absolute_line = fn.start_line + occurrence.line - 1
            attributes: dict[str, Any] = {
                "callee_name": occurrence.callee,
                "occurrence_id": occurrence.call_id,
                "callee_source": occurrence.callee_source,
                "callee_kind": occurrence.callee_kind,
                "argument_sources": list(occurrence.argument_sources),
                "source_range": list(occurrence.source_range or ()),
                "structural_role": occurrence.structural_role,
                "presence_status": occurrence.presence_status,
            }
            if occurrence.receiver_source is not None:
                attributes["receiver_source"] = occurrence.receiver_source
            if occurrence.condition_fact_id is not None:
                attributes["condition_fact_id"] = occurrence.condition_fact_id
            if occurrence.callee_id:
                attributes["callee_id"] = occurrence.callee_id
                attributes["recursive"] = occurrence.callee_id == fn.id
            ref = _add_fact(evidence, _fact(
                target_id=fn.id,
                kind="CALL",
                identity={"occurrence_id": occurrence.call_id},
                owner_id=owner_id,
                file=fn.file_path,
                start_line=absolute_line,
                end_line=absolute_line,
                attributes=attributes,
            ))
            callee_refs.append(ref)
            ordered.append(ref)
            if occurrence.presence_status == "active":
                call_refs_by_line.setdefault(absolute_line, []).append(ref)
            if (
                occurrence.presence_status == "active"
                and occurrence.structural_role == "condition"
            ):
                condition_call_refs_by_fact.setdefault(
                    str(occurrence.condition_fact_id), [],
                ).append(ref)
            if (
                occurrence.presence_status == "active"
                and occurrence.structural_role == "effect"
            ):
                effect_call_refs.append(ref)
    # Linked callee identities are target-level facts because the current common context
    # does not assign every edge to an exact source occurrence.  Never guess that mapping.
    unavailable_callees = {
        str(callee_id)
        for callee_id in ctx.extra.get("unavailable_callee_dependencies") or ()
    }
    unknown_unavailable = unavailable_callees - set(ctx.callees)
    if unknown_unavailable:
        raise ValueError(
            "unavailable callee dependency is not linked from target: "
            f"{sorted(unknown_unavailable)}"
        )
    for callee_id in ctx.callees:
        dependency_status = (
            "unavailable_cycle" if callee_id in unavailable_callees else "linked"
        )
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="CALL",
            identity={"callee_id": callee_id},
            owner_id=owner_id,
            attributes={
                "callee_id": callee_id,
                "recursive": callee_id == fn.id,
                "dependency_status": dependency_status,
            },
        ))
        callee_refs.append(ref)
        linked_callee_refs.append(ref)
        if dependency_status == "unavailable_cycle":
            unavailable_callee_refs.append(ref)

    for symbol in ctx.referenced_symbols:
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="SYMBOL",
            identity={"symbol": symbol.id},
            owner_id=owner_id,
            attributes={
                "symbol_id": symbol.id,
                "name": symbol.name,
                "scope": symbol.kind,
                "data_type": symbol.data_type,
                "declared_value": symbol.declared_value,
                "value_semantics": symbol.value_semantics,
                "initialization_sources": list(symbol.initialization_sources),
            },
        ))
        symbol_refs.append(ref)
        if symbol.kind == "parameter":
            parameter_refs.append(ref)

    for external in ctx.external_evidence:
        external_refs.append(_add_fact(evidence, _fact(
            target_id=fn.id,
            kind="EXTERNAL",
            identity={"chunk": external.id},
            owner_id=owner_id,
            file=external.file_path,
            start_line=external.start_line,
            end_line=external.end_line,
            attributes={
                "chunk_id": external.id,
                "semantic_kind": external.semantic_kind,
                "provenance": external.provenance,
                "matched_key": external.matched_key,
            },
        )))

    data_usages = ctx.data_object_usages
    data_refs_by_line: dict[int, list[str]] = {}
    for index, usage in enumerate(data_usages):
        if not isinstance(usage, DataObjectUsageEvidence):
            raise TypeError(f"unexpected data object usage: {type(usage).__name__}")
        table_id = usage.table_id or usage.raw_reference
        operation = str((ctx.extra.get("table_op") or {}).get(table_id) or usage.access)
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="DATA_OBJECT",
            identity={
                "table": table_id,
                "access": usage.access,
                "line": usage.evidence_line,
                "index": index,
            },
            owner_id=owner_id,
            file=usage.evidence_file,
            start_line=usage.evidence_line,
            end_line=usage.evidence_line,
            attributes={
                "table_id": table_id,
                "access": usage.access,
                "operation": operation,
                "direct": table_id in set(ctx.direct_reads) | set(ctx.direct_writes),
                "resolution": usage.resolution,
                "qualified_columns": [
                    column.as_prompt_dict() for column in usage.qualified_columns
                ],
            },
        ))
        data_refs.append(ref)
        data_refs_by_line.setdefault(usage.evidence_line, []).append(ref)
        ordered.append(ref)
        family = "SELECT" if usage.access == "READ" else _WRITE_FAMILY.get(
            operation.upper(), "UPDATE",
        )
        role = "PURPOSE" if family == "SELECT" else "TARGET"
        _add_slot(
            slots,
            target_id=fn.id,
            family=family,
            role=role,
            anchor={"table": table_id, "line": usage.evidence_line, "access": usage.access},
            target_ref=target_ref,
            evidence_refs=(target_ref, ref),
        )

    if inventory is not None:
        allowed_rule_rows = (
            {int(line) for line in rule_rows}
            if rule_rows is not None else None
        )
        planned_rows = [
            row for row in inventory.table_rows()
            if allowed_rule_rows is None or int(row["line"]) in allowed_rule_rows
        ]
        canonical_rule_evidence = attach_rule_condition_projection(
            rule_evidence(
                inventory, [int(row["line"]) for row in planned_rows],
            ),
            fn.extra.get("_resolved_rule_condition_bundle"),
        )
        canonical_effects = canonical_rule_evidence["effects"]
        canonical_rows = canonical_rule_evidence["rows"]
        for row in planned_rows:
            line = int(row["line"])
            role = str(row.get("role") or "")
            branch = inventory.at_line(line)
            condition = str(row.get("condition") or "")
            canonical_row = canonical_rows[f"R{line}"]
            projected_condition = render_projected_row_condition(
                canonical_row, canonical_rule_evidence,
            )
            direct_effect_refs = tuple(canonical_row.get("direct") or ())
            effects = [dict(canonical_effects[ref]) for ref in direct_effect_refs]
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
                    str(evidence[evidence_ref].attributes.get("node_id") or ""),
                    (),
                )
            )
            # A controller with no direct statement still has one exact outcome:
            # control enters its immediate parser-owned child branches.  Preserve
            # child ownership by projecting only child CONTROL coordinates and
            # conditions here; never copy their statements/calls/effects upward.
            nested_control_refs: tuple[str, ...] = ()
            if branch is not None and row.get("nested_control"):
                nested_control_refs = tuple(
                    _add_fact(evidence, _fact(
                        target_id=fn.id,
                        kind="CONTROL",
                        identity={
                            "nested_child": child.node_id,
                            "parent": branch.node_id,
                        },
                        owner_id=rule_owner_id or owner_id,
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
                own_expression_refs = tuple(
                    expression_refs_by_node.get(branch.node_id, ())
                )
                # A case-value expression has meaning only in relation to its
                # structurally owning controller subject.  Use Parser roles and the
                # parent edge; do not reparse a switch keyword or infer from source.
                owns_case_value = any(
                    evidence[ref].attributes.get("role") == "case_value"
                    for ref in own_expression_refs
                )
                parent_condition_refs = (
                    tuple(
                        ref
                        for ref in expression_refs_by_node.get(
                            branch.parent.node_id, (),
                        )
                        if evidence[ref].attributes.get("role") == "condition"
                    )
                    if owns_case_value and branch.parent is not None else ()
                )
                branch_expression_refs = tuple(dict.fromkeys((
                    *parent_condition_refs, *own_expression_refs,
                )))
            branch_condition_call_refs = tuple(
                call_ref
                for expression_ref in branch_expression_refs
                for call_ref in condition_call_refs_by_fact.get(
                    str(evidence[expression_ref].attributes.get("fact_id") or ""),
                    (),
                )
            )
            supporting_refs = (
                *direct_refs,
                *direct_expression_refs,
                *branch_expression_refs,
                *branch_condition_call_refs,
                *nested_control_refs,
            )
            ref = _add_fact(evidence, _fact(
                target_id=fn.id,
                kind="RULE",
                identity={"line": line, "role": role, "type": row.get("ntype")},
                owner_id=rule_owner_id or owner_id,
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
            planned_rule_refs.append((row, ref, tuple(dict.fromkeys(supporting_refs))))

    ordered = sorted(
        dict.fromkeys(ordered),
        key=lambda ref: (
            evidence[ref].start_line if evidence[ref].start_line > 0 else 2**31,
            evidence[ref].end_line if evidence[ref].end_line > 0 else 2**31,
            evidence[ref].kind,
            ref,
        ),
    )

    is_routine = fn.label in {"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"}
    if is_routine:
        _add_slot(
            slots,
            target_id=fn.id,
            family="FUNCTION",
            role="RESPONSIBILITY",
            anchor="TARGET",
            target_ref=target_ref,
            evidence_refs=(target_ref,),
        )
        if parameter_refs:
            for ref in parameter_refs:
                _add_slot(
                    slots,
                    target_id=fn.id,
                    family="FUNCTION",
                    role="INPUT_ROLE",
                    anchor={"evidence": ref},
                    target_ref=target_ref,
                    evidence_refs=(target_ref, ref),
                )
        elif fn.params.strip():
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="INPUT_ROLE",
                anchor="INTERFACE",
                target_ref=target_ref,
                evidence_refs=(target_ref,),
            )

        for row, rule_ref, supporting_refs in planned_rule_refs:
            if str(row.get("role") or "") not in {
                "decision", "complement", "handler",
            }:
                continue
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="GUARDS_AND_BRANCHES",
                anchor={"evidence": rule_ref},
                target_ref=target_ref,
                evidence_refs=(target_ref, rule_ref, *supporting_refs),
            )

        processing_refs: list[str] = []
        processing_refs.extend(effect_call_refs)
        if not call_refs_by_line:
            processing_refs.extend(linked_callee_refs)
        else:
            processing_refs.extend(unavailable_callee_refs)
        processing_refs.extend(data_refs)
        processing_refs.extend(external_refs)
        processing_refs.extend(
            ref
            for statement, ref, _start, _end in statement_refs
            if any(
                str(effect.get("op") or "") in {"set", "commit", "rollback"}
                for effect in _statement_effects(statement)
            )
        )
        for ref in dict.fromkeys(processing_refs):
            node_id = str(evidence[ref].attributes.get("node_id") or "")
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="PROCESSING_EFFECTS",
                anchor={"evidence": ref},
                target_ref=target_ref,
                evidence_refs=(
                    target_ref, ref, *expression_refs_by_node.get(node_id, ()),
                ),
            )
        return_refs = [
            ref for statement, ref, _start, _end in statement_refs
            if statement.label == "RETURN"
        ]
        for ref in return_refs:
            node_id = str(evidence[ref].attributes.get("node_id") or "")
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="RESULT",
                anchor={"evidence": ref},
                target_ref=target_ref,
                evidence_refs=(
                    target_ref, ref, *expression_refs_by_node.get(node_id, ()),
                ),
            )
        if fn.return_type.strip() and not return_refs:
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="RESULT",
                anchor="INTERFACE",
                target_ref=target_ref,
                evidence_refs=(target_ref,),
            )
        function_roles = {
            spec.role for spec in slots.values() if spec.family == "FUNCTION"
        }
        if (
            not function_roles.issubset(FUNCTION_SUMMARY_ROLES)
            or len(function_roles) > len(FUNCTION_SUMMARY_ROLES)
        ):
            raise ValueError(
                "routine summary exceeded canonical target-owned role boundary: "
                f"{sorted(function_roles)}"
            )
    elif fn.label in callsite_labels():
        # Framework FUNCTION_CALL values are occurrences owned by their routine and stay
        # as exact structural evidence above.  DBMS CALL is an actual analysis CodeNode,
        # so only that target owns a CALL meaning.  Treating every Framework occurrence
        # as a prose owner inflated one APAS run by 4,349 low-level CALL.PURPOSE slots.
        _add_slot(
            slots,
            target_id=fn.id,
            family="CALL",
            role="PURPOSE",
            anchor="TARGET",
            target_ref=target_ref,
            evidence_refs=(target_ref, *unavailable_callee_refs),
        )
    else:
        _add_slot(
            slots,
            target_id=fn.id,
            family="STATEMENT",
            role="DIRECT_ACTION",
            anchor="TARGET",
            target_ref=target_ref,
            evidence_refs=(target_ref,),
        )

    profile = StructuralProfile(
        target=target,
        evidence=evidence,
        child_refs=list(dict.fromkeys(child_refs)),
        ordered_flow=list(dict.fromkeys(ordered)),
        callee_refs=list(dict.fromkeys(callee_refs)),
        data_object_refs=list(dict.fromkeys(data_refs)),
        symbol_refs=list(dict.fromkeys(symbol_refs)),
        expression_refs=list(dict.fromkeys(expression_refs)),
        unsupported=[dict(item) for item in unsupported],
    )
    ordered_slots = tuple(sorted(slots.values(), key=lambda item: item.slot_id))
    return profile, ordered_slots


def semantic_concern_slots(
    slot_specs: Iterable[SlotSpec],
) -> dict[str, tuple[SlotSpec, ...]]:
    """Assign every applicable slot to exactly one existing parallel concern."""
    result: dict[str, list[SlotSpec]] = {
        "summary": [],
        "table_usage_meanings": [],
    }
    for spec in slot_specs:
        if spec.family in {"SELECT", "INSERT", "UPDATE", "DELETE", "UPSERT"}:
            concern = "table_usage_meanings"
        else:
            concern = "summary"
        result[concern].append(spec)
    assigned = [spec.slot_id for specs in result.values() for spec in specs]
    expected = [spec.slot_id for spec in slot_specs]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(expected):
        raise ValueError("semantic concern partition lost or duplicated a slot")
    return {name: tuple(specs) for name, specs in result.items()}


def _statement_effects(statement: StatementFact) -> list[dict[str, str]]:
    if statement.label == "ASSIGNMENT":
        return [{
            "op": "set",
            "target": statement.target,
            "value": statement.expression,
        }]
    op = {
        "RETURN": "ret",
        "THROW": "raise_error",
        "RAISE": "raise_error",
        "BREAK": "jump",
        "CONTINUE": "jump",
        "GOTO": "jump",
        "EXIT": "exit_loop",
        "COMMIT": "commit",
        "ROLLBACK": "rollback",
    }.get(statement.label)
    return [{"op": op, "target": statement.target, "value": statement.expression}] if op else []
