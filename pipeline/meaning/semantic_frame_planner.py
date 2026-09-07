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
from pipeline.source_languages import callsite_labels, control_structure_labels
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SlotSpec,
    StructuralProfile,
    StructuralTarget,
)
from pipeline.checks.branch_inventory import BranchInventory
from pipeline.meaning.rule_plan import append_rule_facts
from pipeline.meaning.semantic_facts import add_fact, add_slot, make_fact


_EXPRESSION_ROLES = frozenset({
    "condition", "case_value", "return_value", "assignment_target",
    "assignment_value", "initializer_value", "update_expression",
})
_EXPRESSION_KINDS = frozenset({
    "reference", "literal", "group", "member", "subscript", "call", "unary",
    "binary", "conditional", "assignment", "cast", "sequence",
})


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
    unavailable_callee_refs: list[str] = []
    external_refs: list[str] = []
    data_refs: list[str] = []
    symbol_refs: list[str] = []
    expression_refs: list[str] = []
    expression_refs_by_node: dict[str, list[str]] = {}
    target_ref = add_fact(evidence, make_fact(
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
        ref = add_fact(evidence, make_fact(
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
        ref = add_fact(evidence, make_fact(
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
        ref = add_fact(evidence, make_fact(
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
            ref = add_fact(evidence, make_fact(
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
            if occurrence.presence_status == "active":
                ordered.append(ref)
                call_refs_by_line.setdefault(absolute_line, []).append(ref)
            if (
                occurrence.presence_status == "active"
                and occurrence.structural_role == "condition"
            ):
                condition_call_refs_by_fact.setdefault(
                    str(occurrence.condition_fact_id), [],
                ).append(ref)
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
        ref = add_fact(evidence, make_fact(
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
        if dependency_status == "unavailable_cycle":
            unavailable_callee_refs.append(ref)

    for symbol in ctx.referenced_symbols:
        ref = add_fact(evidence, make_fact(
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

    for external in ctx.external_evidence:
        external_refs.append(add_fact(evidence, make_fact(
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
        ref = add_fact(evidence, make_fact(
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

    if inventory is not None:
        append_rule_facts(
            fn=fn,
            inventory=inventory,
            rule_rows=rule_rows,
            rule_owner_id=rule_owner_id,
            evidence=evidence,
            ordered=ordered,
            statement_refs=statement_refs,
            call_refs_by_line=call_refs_by_line,
            data_refs_by_line=data_refs_by_line,
            expression_refs_by_node=expression_refs_by_node,
            condition_call_refs_by_fact=condition_call_refs_by_fact,
        )

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
        summary_evidence = tuple(dict.fromkeys((
            target_ref,
            *ordered,
            *unavailable_callee_refs,
            *external_refs,
        )))
        add_slot(
            slots,
            target_id=fn.id,
            family="FUNCTION",
            role="WHOLE_FUNCTION",
            anchor="TARGET",
            target_ref=target_ref,
            evidence_refs=summary_evidence,
        )
    elif fn.label in callsite_labels():
        # Framework FUNCTION_CALL values are occurrences owned by their routine and stay
        # as exact structural evidence above.  DBMS CALL is an actual analysis CodeNode,
        # so only that target owns a CALL meaning.  Treating every Framework occurrence
        # as a prose owner inflated one APAS run by 4,349 low-level CALL.PURPOSE slots.
        add_slot(
            slots,
            target_id=fn.id,
            family="CALL",
            role="PURPOSE",
            anchor="TARGET",
            target_ref=target_ref,
            evidence_refs=(target_ref, *unavailable_callee_refs),
        )
    else:
        add_slot(
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
    """Assign every model-owned slot to the single Summary concern.

    Table access and column occurrences stay as deterministic evidence.  They do not
    create prose obligations or a second copy of the same meaning.
    """
    specs = tuple(slot_specs)
    assigned = [spec.slot_id for spec in specs]
    expected = [spec.slot_id for spec in specs]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(expected):
        raise ValueError("semantic concern partition lost or duplicated a slot")
    return {"summary": specs}


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
