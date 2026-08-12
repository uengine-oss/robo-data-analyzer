"""Project existing parser/linker facts into bounded semantic frame slots."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, StatementFact
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.languages import control_structure_labels
from pipeline.models.semantic_frame import (
    EvidenceFact,
    SlotSpec,
    StructuralProfile,
    StructuralTarget,
    evidence_id,
    slot_id,
    stable_digest,
)
from pipeline.quality_gates.branch_inventory import BranchInventory


_WRITE_FAMILY = {
    "INSERT": "INSERT",
    "UPDATE": "UPDATE",
    "DELETE": "DELETE",
    "TRUNCATE": "DELETE",
    "MERGE": "UPSERT",
}


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
    data_refs: list[str] = []
    symbol_refs: list[str] = []

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
    for relative_line, call_name in tuple(ctx.extra.get("ast_call_lines") or ()):
        absolute_line = fn.start_line + int(relative_line) - 1
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="CALL",
            identity={"line": absolute_line, "name": call_name},
            owner_id=owner_id,
            file=fn.file_path,
            start_line=absolute_line,
            end_line=absolute_line,
            attributes={
                "callee_id": "",
                "callee_name": str(call_name),
                "scope": "",
                "recursive": False,
                "arguments_source": "",
                "result_target": "",
            },
        ))
        call_refs_by_line.setdefault(absolute_line, []).append(ref)
        callee_refs.append(ref)
        ordered.append(ref)
        _add_slot(
            slots,
            target_id=fn.id,
            family="CALL",
            role="PURPOSE",
            anchor={"line": absolute_line, "call": call_name},
            target_ref=target_ref,
            evidence_refs=(target_ref, ref),
        )

    # Linked callee identities are target-level facts because the current common context
    # does not assign every edge to an exact source occurrence.  Never guess that mapping.
    for callee_id in ctx.callees:
        ref = _add_fact(evidence, _fact(
            target_id=fn.id,
            kind="CALL",
            identity={"callee_id": callee_id},
            owner_id=owner_id,
            attributes={
                "callee_id": callee_id,
                "callee_name": "",
                "scope": "internal",
                "recursive": callee_id == fn.id,
                "arguments_source": "",
                "result_target": "",
            },
        ))
        callee_refs.append(ref)

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

    for external in ctx.external_evidence:
        _add_fact(evidence, _fact(
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
        ))

    data_usages = tuple(ctx.extra.get("data_object_usages") or ())
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

    rule_refs: list[str] = []
    if inventory is not None:
        allowed_rule_rows = (
            {int(line) for line in rule_rows}
            if rule_rows is not None else None
        )
        for row in inventory.table_rows():
            line = int(row["line"])
            if allowed_rule_rows is not None and line not in allowed_rule_rows:
                continue
            role = str(row.get("role") or "")
            branch = inventory.at_line(line)
            condition = str(row.get("condition") or "")
            effects = [
                {"op": op, "target": target}
                for op, target in inventory.direct_statement_effects(line)
            ]
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
                    "condition": condition,
                    "effects": effects,
                    "when": [],
                    "source_refs": [],
                    "type": str(row.get("ntype") or ""),
                    "condition_timing": str(row.get("condition_timing") or ""),
                    "complement_of": row.get("complement_of"),
                    "implicit_false": bool("implicit_false" in condition),
                },
            ))
            rule_refs.append(ref)
            ordered.append(ref)
            _control_slots(
                slots,
                target_id=fn.id,
                target_ref=target_ref,
                row=row,
                branch=branch,
                rule_ref=ref,
                statement_refs=statement_refs,
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
        all_direct = tuple(dict.fromkeys((target_ref, *ordered, *rule_refs, *data_refs)))
        _add_slot(
            slots,
            target_id=fn.id,
            family="FUNCTION",
            role="RESPONSIBILITY",
            anchor="TARGET",
            target_ref=target_ref,
            evidence_refs=all_direct,
        )
        if fn.params.strip() or any(symbol.kind == "parameter" for symbol in ctx.referenced_symbols):
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="INPUT_ROLE",
                anchor="TARGET",
                target_ref=target_ref,
                evidence_refs=(target_ref, *symbol_refs),
            )
        return_refs = [
            ref for statement, ref, _start, _end in statement_refs
            if statement.label == "RETURN"
        ]
        if fn.return_type.strip() or return_refs:
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="RESULT",
                anchor="TARGET",
                target_ref=target_ref,
                evidence_refs=(target_ref, *return_refs),
            )
        flow_refs = tuple(dict.fromkeys(ordered))
        if len(flow_refs) >= 2:
            _add_slot(
                slots,
                target_id=fn.id,
                family="FUNCTION",
                role="ORDERED_FLOW",
                anchor="TARGET",
                target_ref=target_ref,
                evidence_refs=(target_ref, *flow_refs),
            )
    else:
        direct = tuple(dict.fromkeys((target_ref, *ordered, *rule_refs, *data_refs)))
        _add_slot(
            slots,
            target_id=fn.id,
            family="STATEMENT",
            role="DIRECT_ACTION",
            anchor="TARGET",
            target_ref=target_ref,
            evidence_refs=direct,
        )

    profile = StructuralProfile(
        target=target,
        evidence=evidence,
        child_refs=list(dict.fromkeys(child_refs)),
        ordered_flow=list(dict.fromkeys(ordered)),
        callee_refs=list(dict.fromkeys(callee_refs)),
        data_object_refs=list(dict.fromkeys(data_refs)),
        symbol_refs=list(dict.fromkeys(symbol_refs)),
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
        "rules": [],
        "insights": [],
    }
    for spec in slot_specs:
        if spec.family in {"SELECT", "INSERT", "UPDATE", "DELETE", "UPSERT"}:
            concern = "insights"
        elif spec.family in {"DECISION", "LOOP", "EXCEPTION"}:
            concern = "rules"
        elif spec.family == "STATEMENT" and not spec.slot_id.endswith(":TARGET"):
            concern = "rules"
        else:
            concern = "summary"
        result[concern].append(spec)
    assigned = [spec.slot_id for specs in result.values() for spec in specs]
    expected = [spec.slot_id for spec in slot_specs]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(expected):
        raise ValueError("semantic concern partition lost or duplicated a slot")
    return {name: tuple(specs) for name, specs in result.items()}


def rule_slot_specs(
    profile: StructuralProfile,
    slot_specs: Iterable[SlotSpec],
    rows: Iterable[int],
) -> tuple[SlotSpec, ...]:
    assigned = {int(row) for row in rows}
    result: list[SlotSpec] = []
    covered: set[int] = set()
    for spec in slot_specs:
        anchors = {
            int(fact.attributes["anchor_line"])
            for ref in spec.evidence_refs
            if (fact := profile.evidence[ref]).kind == "RULE"
            and fact.attributes.get("anchor_line") is not None
        }
        if anchors & assigned:
            result.append(spec)
            covered.update(anchors & assigned)
    missing = assigned - covered
    if missing:
        raise ValueError(f"assigned rule rows lack semantic slots: {sorted(missing)}")
    return tuple(result)


def slot_rule_anchor(profile: StructuralProfile, spec: SlotSpec) -> int | None:
    anchors = {
        int(fact.attributes["anchor_line"])
        for ref in spec.evidence_refs
        if (fact := profile.evidence[ref]).kind == "RULE"
        and fact.attributes.get("anchor_line") is not None
    }
    if len(anchors) > 1:
        raise ValueError(f"semantic slot spans multiple rule anchors: {spec.slot_id}")
    return next(iter(anchors), None)


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


def _control_slots(
    slots: dict[str, SlotSpec],
    *,
    target_id: str,
    target_ref: str,
    row: Mapping[str, Any],
    branch: object | None,
    rule_ref: str,
    statement_refs: Iterable[tuple[StatementFact, str, int, int]],
) -> None:
    line = int(row["line"])
    role = str(row.get("role") or "")
    ntype = str(row.get("ntype") or "").upper()
    owned_statements = tuple(
        ref for _statement, ref, start, end in statement_refs
        if start <= line <= end
    )
    refs = (target_ref, rule_ref, *owned_statements)
    direct_terminates = bool(getattr(branch, "direct_terminates", False))

    if role == "handler" or ntype in {"CATCH", "EXCEPTION"}:
        _add_slot(
            slots, target_id=target_id, family="EXCEPTION", role="FAILURE",
            anchor=line, target_ref=target_ref, evidence_refs=refs,
        )
        _add_slot(
            slots, target_id=target_id, family="EXCEPTION", role="HANDLING",
            anchor=line, target_ref=target_ref, evidence_refs=refs,
        )
        if direct_terminates or any(
            statement.label in {"THROW", "RAISE", "RETURN", "GOTO"}
            for statement, _ref, start, end in statement_refs
            if start <= line <= end
        ):
            _add_slot(
                slots, target_id=target_id, family="EXCEPTION", role="PROPAGATION",
                anchor=line, target_ref=target_ref, evidence_refs=refs,
            )
        return

    if ntype == "LOOP":
        condition = str(row.get("condition") or "")
        if condition:
            _add_slot(
                slots, target_id=target_id, family="LOOP", role="ITERATION_TARGET",
                anchor=line, target_ref=target_ref, evidence_refs=refs,
            )
        _add_slot(
            slots, target_id=target_id, family="LOOP", role="PER_ITEM_ACTION",
            anchor=line, target_ref=target_ref, evidence_refs=refs,
        )
        if str(row.get("condition_timing") or "") or direct_terminates or any(
            statement.label in {"BREAK", "RETURN", "EXIT"}
            for statement, _ref, start, end in statement_refs
            if start <= line <= end
        ):
            _add_slot(
                slots, target_id=target_id, family="LOOP", role="TERMINATION",
                anchor=line, target_ref=target_ref, evidence_refs=refs,
            )
        return

    if role == "decision":
        _add_slot(
            slots, target_id=target_id, family="DECISION", role="ROLE",
            anchor=line, target_ref=target_ref, evidence_refs=refs,
        )
        _add_slot(
            slots, target_id=target_id, family="DECISION", role="TRUE_OUTCOME",
            anchor=line, target_ref=target_ref, evidence_refs=refs,
        )
        condition = str(row.get("condition") or "")
        if row.get("complement_of") or "implicit_false" in condition:
            _add_slot(
                slots, target_id=target_id, family="DECISION", role="FALSE_OUTCOME",
                anchor=line, target_ref=target_ref, evidence_refs=refs,
            )
    elif role in {"entry", "continuation", "exit", "controller", "complement"}:
        _add_slot(
            slots, target_id=target_id, family="STATEMENT", role="DIRECT_ACTION",
            anchor=line, target_ref=target_ref, evidence_refs=refs,
        )
