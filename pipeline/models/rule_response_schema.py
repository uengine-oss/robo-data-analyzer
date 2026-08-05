"""Dynamic closed schemas for model-owned branch Rule meaning.

The wire contract never asks a weak model to hand-write Python syntax.  The model owns
condition references, polarity, effects, and narrative; deterministic code only renders
that structured answer into the canonical Python Rule notation used by validators and
graph consumers.
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
from pipeline.models.rule_expression import NL_PATTERN
from pipeline.quality_gates.rule_prompt_evidence import render_condition_refs


ConditionRef = Annotated[str, Field(pattern=r"^!?C[1-9][0-9]*$")]
EffectRef = Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]


class AssignedEffect(BaseModel):
    """One fixed-shape effect; null positions make the grammar small and closed."""

    model_config = ConfigDict(extra="forbid")

    op: Literal[
        "call", "ret", "set", "raise_error", "jump",
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

    cond: list[ConditionRef] = Field(
        ...,
        description="model-selected condition refs and polarity; empty means unconditional",
    )
    then: list[EffectRef] = Field(
        ...,
        description="model-selected direct evidence effect refs",
    )
    extra: list[AssignedExtraEffect] = Field(
        ...,
        description=(
            "rare source-grounded semantic effects absent from the direct evidence "
            "catalog; normally empty"
        ),
    )
    nl: str = Field(
        ...,
        pattern=NL_PATTERN,
        max_length=300,
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
    condition_sources: Mapping[str, str] | None = None,
    effect_sources: Mapping[str, Mapping[str, object]] | None = None,
) -> RulesExamplesOutput:
    """Render validated model meaning into canonical Python Rule notation."""
    assigned = _assigned_rows(rows)
    sources = condition_sources or {}
    effects = effect_sources or {}
    return RulesExamplesOutput(
        rules=[
            RuleItem.model_construct(
                anchor_line=row,
                cond=render_condition_refs(
                    getattr(output.rules, f"R{row}").cond, sources,
                ),
                then=[
                    _render_selected_effect(effect, effects)
                    for effect in getattr(output.rules, f"R{row}").then
                ] + [
                    _render_selected_effect(effect, effects)
                    for effect in getattr(output.rules, f"R{row}").extra
                ],
                nl=getattr(output.rules, f"R{row}").nl,
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
    condition_sources: Mapping[str, Mapping[str, str]] | None = None,
    effect_sources: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None,
) -> BatchRulesOutput:
    """Convert DBMS fixed-key leaves without deterministic substitution."""
    nodes: list[BatchRulesItem] = []
    sources_by_node = condition_sources or {}
    effects_by_node = effect_sources or {}
    for node_key, rows in assignments.items():
        item = getattr(output.nodes, node_key)
        assigned = _assigned_rows(rows) if rows else ()
        rules = [
            RuleItem.model_construct(
                anchor_line=row,
                cond=render_condition_refs(
                    getattr(item.rules, f"R{row}").cond,
                    sources_by_node.get(node_key, {}),
                ),
                then=[
                    _render_selected_effect(
                        effect, effects_by_node.get(node_key, {}),
                    )
                    for effect in getattr(item.rules, f"R{row}").then
                ] + [
                    _render_selected_effect(
                        effect, effects_by_node.get(node_key, {}),
                    )
                    for effect in getattr(item.rules, f"R{row}").extra
                ],
                nl=getattr(item.rules, f"R{row}").nl,
            )
            for row in assigned
        ]
        nodes.append(BatchRulesItem(node_key=node_key, rules=rules))
    return BatchRulesOutput(nodes=nodes)


def _quoted(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


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
    if effect.op in {"select", "insert", "update", "delete", "upsert"}:
        return f"{effect.op}({_quoted(effect.target)})"
    raise TypeError(f"unsupported assigned effect: {type(effect).__name__}")


def _effect_positions_valid(effect: AssignedEffect | AssignedExtraEffect) -> bool:
    if effect.op == "set":
        return bool(effect.target) and bool(effect.value) and effect.from_source is not None
    if effect.op == "ret":
        return effect.target is None and bool(effect.value) and effect.from_source is not None
    if effect.op in {"call", "jump", "select", "insert", "update", "delete", "upsert"}:
        return bool(effect.target) and effect.value is None and effect.from_source is None
    return effect.target is None and effect.value is None and effect.from_source is None
