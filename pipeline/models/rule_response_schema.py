"""Dynamic closed schemas for model-owned branch Rule narrative.

The parser-owned evidence already fixes each row's control path and direct effects.
The model therefore owns only the human business narrative; one deterministic boundary
materializes ``cond`` and ``then`` from that same evidence for validators and graph
consumers.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    BatchRulesOutput,
    RuleItem,
    RulesExamplesOutput,
)
from pipeline.models.rule_expression import NL_MAX_CHARS, NL_PATTERN
from pipeline.quality_gates.nl_reference_safety import replace_groundable_references
from pipeline.quality_gates.rule_prompt_evidence import render_row_condition
from pipeline.models.semantic_frame import (
    SlotSpec,
    StructuralProfile,
    normalize_semantic_frame_response,
    semantic_frame_response_model,
    stable_digest,
)
from pipeline.models.semantic_frame_projection import project_rule_narrative


ConditionRef = Annotated[str, Field(pattern=r"^!?C[1-9][0-9]*$")]
EffectRef = Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]
RULE_NL_MAX_LENGTH = NL_MAX_CHARS


class AssignedEffect(BaseModel):
    """One fixed-shape effect; null positions make the grammar small and closed."""

    model_config = ConfigDict(extra="forbid")

    op: Literal[
        "call", "ret", "set", "raise_error", "jump", "exit_loop", "commit", "rollback",
        "select", "insert", "update", "delete", "upsert",
    ]
    target: str | None = Field(...)
    value: str | None = Field(...)
    from_source: bool | None = Field(...)


class AssignedExtraEffect(BaseModel):
    """Only semantic effects not already represented by direct CALL/RETURN/assignment."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["raise_error", "select", "insert", "update", "delete", "upsert"]
    target: str | None = Field(...)
    value: str | None = Field(...)
    from_source: bool | None = Field(...)


class AssignedRuleLeaf(BaseModel):
    """One shared wire definition referenced by every deterministic row key."""

    model_config = ConfigDict(extra="forbid")

    nl: str = Field(
        ...,
        pattern=NL_PATTERN,
        max_length=RULE_NL_MAX_LENGTH,
        description="human business meaning",
    )


def _assigned_rows(rows: Sequence[int]) -> tuple[int, ...]:
    assigned = tuple(int(row) for row in rows)
    if not assigned or any(row <= 0 for row in assigned):
        raise ValueError("assigned rule rows must be positive and non-empty")
    if len(assigned) != len(set(assigned)):
        raise ValueError(f"duplicate assigned rule rows: {assigned!r}")
    return assigned


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _rules_map_model(rows: tuple[int, ...], fingerprint: str) -> type[BaseModel]:
    fields = {
        f"R{row}": (AssignedRuleLeaf, Field(...))
        for row in rows
    }
    return create_model(
        f"AssignedRuleMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def rules_response_model(rows: Sequence[int]) -> type[BaseModel]:
    """Require every assigned row and forbid any unassigned row."""
    assigned = _assigned_rows(rows)
    fingerprint = _fingerprint({"rows": assigned})
    rules_model = _rules_map_model(assigned, fingerprint)
    return create_model(
        f"AssignedRulesOutput_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        rules=(rules_model, Field(...)),
    )


def normalize_rules_response(
    output: BaseModel,
    rows: Sequence[int],
    evidence: Mapping[str, object],
) -> RulesExamplesOutput:
    """Bind model narrative to parser-owned conditions and direct effects."""
    assigned = _assigned_rows(rows)
    conditions, effects, evidence_rows = _evidence_maps(evidence)
    return RulesExamplesOutput(
        rules=[
            RuleItem.model_construct(
                anchor_line=row,
                cond=render_row_condition(
                    _evidence_row(evidence_rows, row), conditions,
                ),
                then=[
                    _render_selected_effect(effect, effects)
                    for effect in (_evidence_row(evidence_rows, row).get("direct") or ())
                ],
                nl=_ground_narrative_references(
                    getattr(output.rules, f"R{row}").nl,
                    _evidence_row(evidence_rows, row),
                    conditions,
                    effects,
                ),
            )
            for row in assigned
        ],
    )


def batch_rules_response_model(
    assignments: Mapping[str, Sequence[int]],
) -> type[BaseModel]:
    """Require every DBMS node key and every assigned row key exactly once."""
    if not assignments:
        raise ValueError("DBMS rule assignments must not be empty")
    normalized = {
        str(node_key): tuple(int(row) for row in rows)
        for node_key, rows in assignments.items()
    }
    if any(not key for key in normalized):
        raise ValueError("DBMS node_key must not be empty")
    fingerprint = _fingerprint({"assignments": normalized})
    node_fields = {}
    for node_key, rows in normalized.items():
        if rows:
            assigned = _assigned_rows(rows)
            rules_model = _rules_map_model(
                assigned,
                f"{fingerprint}_{_fingerprint(node_key)}",
            )
        else:
            rules_model = create_model(
                f"AssignedRuleMap_{fingerprint}_{_fingerprint(node_key)}",
                __config__=ConfigDict(extra="forbid"),
            )
        item_model = create_model(
            f"AssignedNodeRules_{fingerprint}_{_fingerprint(node_key)}",
            __config__=ConfigDict(extra="forbid"),
            rules=(rules_model, Field(...)),
        )
        node_fields[node_key] = (item_model, Field(...))
    nodes_model = create_model(
        f"AssignedNodeMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **node_fields,
    )
    return create_model(
        f"AssignedBatchRulesOutput_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        nodes=(nodes_model, Field(...)),
    )


def normalize_batch_rules_response(
    output: BaseModel,
    assignments: Mapping[str, Sequence[int]],
    evidences: Mapping[str, Mapping[str, object]],
) -> BatchRulesOutput:
    """Bind every DBMS narrative leaf to its node's parser-owned evidence."""
    nodes: list[BatchRulesItem] = []
    for node_key, rows in assignments.items():
        item = getattr(output.nodes, node_key)
        assigned = _assigned_rows(rows) if rows else ()
        evidence = evidences.get(node_key)
        if evidence is None:
            raise ValueError(f"rule evidence missing for node: {node_key!r}")
        conditions, effects, evidence_rows = _evidence_maps(evidence)
        rules = [
            RuleItem.model_construct(
                anchor_line=row,
                cond=render_row_condition(
                    _evidence_row(evidence_rows, row), conditions,
                ),
                then=[
                    _render_selected_effect(effect, effects)
                    for effect in (_evidence_row(evidence_rows, row).get("direct") or ())
                ],
                nl=_ground_narrative_references(
                    getattr(item.rules, f"R{row}").nl,
                    _evidence_row(evidence_rows, row),
                    conditions,
                    effects,
                ),
            )
            for row in assigned
        ]
        nodes.append(BatchRulesItem(node_key=node_key, rules=rules))
    return BatchRulesOutput(nodes=nodes)


def _evidence_maps(
    evidence: Mapping[str, object],
) -> tuple[Mapping[str, str], Mapping[str, Mapping[str, object]], Mapping[str, object]]:
    conditions = evidence.get("conditions")
    effects = evidence.get("effects")
    rows = evidence.get("rows")
    if not isinstance(conditions, Mapping) or not isinstance(effects, Mapping) or not isinstance(rows, Mapping):
        raise ValueError("rule evidence requires conditions, effects, and rows maps")
    return conditions, effects, rows


def _evidence_row(rows: Mapping[str, object], row: int) -> Mapping[str, object]:
    item = rows.get(f"R{row}")
    if not isinstance(item, Mapping):
        raise ValueError(f"rule evidence row missing: R{row}")
    return item


def _quoted(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _ground_narrative_references(
    narrative: str,
    row: Mapping[str, object],
    conditions: Mapping[str, str],
    effects: Mapping[str, Mapping[str, object]],
) -> str:
    """Replace leaked private coordinates with their exact parser-owned evidence."""
    def grounded(reference: str) -> str:
        if reference.startswith("C"):
            source = conditions.get(reference)
            return f"({source})" if source is not None else reference
        if reference.startswith("E"):
            return _render_selected_effect(reference, effects)
        return reference

    # Grounding can expand a legacy-coordinate response beyond its old wire limit.
    # The semantic frame remains the lossless contract, and this compatibility
    # projection must prefer exact source text over either truncation or leaked IDs.
    return replace_groundable_references(narrative, row, conditions, grounded)


def _render_selected_effect(
    selection: EffectRef | AssignedEffect | AssignedExtraEffect,
    effect_sources: Mapping[str, Mapping[str, object]],
) -> str:
    if isinstance(selection, str):
        payload = effect_sources.get(selection)
        if payload is None:
            return f"invalid_effect({_quoted(selection)})"
        return _render_effect(AssignedEffect.model_validate(payload))
    return _render_effect(selection)


def _render_effect(effect: AssignedEffect | AssignedExtraEffect) -> str:
    if not _effect_positions_valid(effect):
        payload = effect.model_dump_json()
        return f"invalid_effect({_quoted(payload)})"
    if effect.op == "call":
        return f"call({_quoted(effect.target)})"
    if effect.op == "jump":
        return f"jump({_quoted(effect.target)})"
    if effect.op == "ret":
        if effect.value is None:
            return "ret(None)"
        value = (
            f"F({_quoted(effect.value)})"
            if effect.from_source else _quoted(effect.value)
        )
        return f"ret({value})"
    if effect.op == "set":
        value = (
            f"F({_quoted(effect.value)})"
            if effect.from_source else _quoted(effect.value)
        )
        return f"set(F({_quoted(effect.target)}), {value})"
    if effect.op == "raise_error":
        return "raise_error()"
    if effect.op in {"exit_loop", "commit", "rollback"}:
        return f"{effect.op}()"
    if effect.op in {"select", "insert", "update", "delete", "upsert"}:
        return f"{effect.op}({_quoted(effect.target)})"
    raise TypeError(f"unsupported assigned effect: {type(effect).__name__}")


def _effect_positions_valid(effect: AssignedEffect | AssignedExtraEffect) -> bool:
    if effect.op == "set":
        return bool(effect.target) and bool(effect.value) and effect.from_source is not None
    if effect.op == "ret":
        return effect.target is None and (
            (bool(effect.value) and effect.from_source is not None)
            or (effect.value is None and effect.from_source is None)
        )
    if effect.op in {"call", "jump", "select", "insert", "update", "delete", "upsert"}:
        return bool(effect.target) and effect.value is None and effect.from_source is None
    return effect.target is None and effect.value is None and effect.from_source is None


def frame_rules_response_model(slot_specs: Sequence[SlotSpec]) -> type[BaseModel]:
    """Require every rule-owned semantic slot and expose no coordinate leaf fields."""
    return semantic_frame_response_model(slot_specs)


def normalize_frame_rules_response(
    output: BaseModel,
    profile: StructuralProfile,
    slot_specs: Sequence[SlotSpec],
    rows: Sequence[int],
    evidence: Mapping[str, object],
) -> RulesExamplesOutput:
    assigned = _assigned_rows(rows)
    frame = normalize_semantic_frame_response(output, profile, slot_specs)
    conditions, effects, evidence_rows = _evidence_maps(evidence)
    rules: list[RuleItem] = []
    for row in assigned:
        row_evidence = _evidence_row(evidence_rows, row)
        raw_narrative = project_rule_narrative(frame, row)
        narrative = _ground_narrative_references(
            raw_narrative, row_evidence, conditions, effects,
        )
        rules.append(RuleItem.model_construct(
            anchor_line=row,
            cond=render_row_condition(row_evidence, conditions),
            then=[
                _render_selected_effect(effect, effects)
                for effect in (row_evidence.get("direct") or ())
            ],
            nl=narrative,
        ))
    return RulesExamplesOutput(rules=rules, semantic_frame=frame)


def batch_frame_rules_response_model(
    assignments: Mapping[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
) -> type[BaseModel]:
    if not assignments:
        raise ValueError("frame rule assignments must not be empty")
    fingerprint = stable_digest({
        key: [spec.model_dump(mode="json") for spec in specs]
        for key, (_profile, specs) in assignments.items()
    })
    fields = {}
    for node_key, (_profile, specs) in assignments.items():
        slot_model = semantic_frame_response_model(specs)
        slots_type = slot_model.model_fields["slots"].annotation
        item_model = create_model(
            f"AssignedFrameRuleItem_{fingerprint}_{stable_digest(node_key)}",
            __config__=ConfigDict(extra="forbid"),
            slots=(slots_type, Field(...)),
        )
        fields[node_key] = (item_model, Field(...))
    nodes_model = create_model(
        f"AssignedFrameRuleMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    return create_model(
        f"AssignedBatchFrameRules_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        nodes=(nodes_model, Field(...)),
    )


def normalize_batch_frame_rules_response(
    output: BaseModel,
    assignments: Mapping[str, tuple[StructuralProfile, tuple[SlotSpec, ...]]],
    rows_by_node: Mapping[str, Sequence[int]],
    evidences: Mapping[str, Mapping[str, object]],
) -> BatchRulesOutput:
    nodes: list[BatchRulesItem] = []
    for node_key, (profile, specs) in assignments.items():
        item = getattr(output.nodes, node_key)
        normalized = normalize_frame_rules_response(
            item,
            profile,
            specs,
            rows_by_node[node_key],
            evidences[node_key],
        )
        nodes.append(BatchRulesItem(
            node_key=node_key,
            rules=normalized.rules,
            semantic_frame=normalized.semantic_frame,
        ))
    return BatchRulesOutput(nodes=nodes)
