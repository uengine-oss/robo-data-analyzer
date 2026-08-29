"""Materialize canonical Rule leaves from parser-owned branch evidence.

Rule conditions, effects, coordinates, ownership, and Examples are structural facts.
They must not pass through a model response before graph/API/MCP consumers can use
them.  This module is the single conversion boundary from ``rule_evidence`` to the
product ``RuleItem`` projection.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pipeline.models.code_analysis_output import RuleItem
from pipeline.quality_gates.rule_condition_rendering import (
    render_projected_row_condition,
)


class StructuralEffect(BaseModel):
    """Closed parser-owned effect shape used by deterministic Rule rendering."""

    model_config = ConfigDict(extra="forbid")

    op: Literal[
        "call", "ret", "set", "raise_error", "jump", "exit_loop", "commit",
        "rollback", "select", "insert", "update", "delete", "upsert",
    ]
    target: str | None
    value: str | None
    from_source: bool | None
    occurrence_id: str | None = None
    call_source: str | None = None
    argument_sources: tuple[str, ...] = ()
    receiver_source: str | None = None
    callee_id: str = ""


def materialize_rule_items(
    evidence: Mapping[str, object],
    rows: Sequence[int],
) -> list[RuleItem]:
    """Create exact structural ``cond``/``then`` Rules before meaning projection.

    The input must cover the assigned rows exactly.  An invariant mismatch is an
    operational defect, not a model-quality finding, so callers let ``ValueError``
    fail the phase instead of silently returning fewer Rules.
    """
    assigned = tuple(int(row) for row in rows)
    if len(assigned) != len(set(assigned)) or any(row <= 0 for row in assigned):
        raise ValueError(
            f"materialized rule rows must be unique and positive: {assigned}"
        )
    conditions, effects, evidence_rows = _evidence_maps(evidence)
    expected = {f"R{row}" for row in assigned}
    if set(evidence_rows) != expected:
        raise ValueError(
            "rule evidence must exactly cover assigned rows: "
            f"expected={sorted(expected)}, actual={sorted(evidence_rows)}"
        )
    return [
        RuleItem(
            anchor_line=row,
            structural_condition=render_projected_row_condition(
                _evidence_row(evidence_rows, row), evidence,
            ),
            structural_effects=[
                _render_selected_effect(effect_ref, effects)
                for effect_ref in (
                    _evidence_row(evidence_rows, row).get("direct") or ()
                )
            ],
        )
        for row in assigned
    ]


def _evidence_maps(
    evidence: Mapping[str, object],
) -> tuple[Mapping[str, str], Mapping[str, Mapping[str, object]], Mapping[str, object]]:
    conditions = evidence.get("conditions")
    effects = evidence.get("effects")
    rows = evidence.get("rows")
    if (
        not isinstance(conditions, Mapping)
        or not isinstance(effects, Mapping)
        or not isinstance(rows, Mapping)
    ):
        raise ValueError("rule evidence requires conditions, effects, and rows maps")
    return conditions, effects, rows


def _evidence_row(rows: Mapping[str, object], row: int) -> Mapping[str, object]:
    item = rows.get(f"R{row}")
    if not isinstance(item, Mapping):
        raise ValueError(f"rule evidence row missing: R{row}")
    return item


def _quoted(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _render_selected_effect(
    selection: object,
    effect_sources: Mapping[str, Mapping[str, object]],
) -> str:
    reference = str(selection)
    payload = effect_sources.get(reference)
    if payload is None:
        raise ValueError(f"rule effect evidence missing: {reference}")
    return render_structural_effect(payload)


def render_structural_effect(
    value: StructuralEffect | Mapping[str, object],
) -> str:
    """Render one closed structural effect without adding business meaning."""
    effect = validate_structural_effect(value)
    if effect.op == "call":
        receiver = (
            "None"
            if effect.receiver_source is None
            else f"F({_quoted(effect.receiver_source)})"
        )
        arguments = ", ".join(
            f"F({_quoted(argument)})"
            for argument in effect.argument_sources
        )
        return f"call({_quoted(effect.target)}, {receiver}, [{arguments}])"
    if effect.op == "jump":
        return f"jump({_quoted(effect.target)})"
    if effect.op == "ret":
        value = "None" if effect.value is None else (
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
    raise ValueError(f"unsupported parser-owned Rule effect op: {effect.op!r}")


def validate_structural_effect(
    value: StructuralEffect | Mapping[str, object],
) -> StructuralEffect:
    """Return one exact Rule effect or reject unknown and incomplete fields."""
    effect = (
        value
        if isinstance(value, StructuralEffect)
        else StructuralEffect.model_validate(value)
    )
    if not _effect_positions_valid(effect):
        raise ValueError(
            "invalid parser-owned Rule effect shape: " + effect.model_dump_json()
        )
    return effect


def _effect_positions_valid(effect: StructuralEffect) -> bool:
    has_call_metadata = any((
        effect.occurrence_id is not None,
        effect.call_source is not None,
        bool(effect.argument_sources),
        effect.receiver_source is not None,
        bool(effect.callee_id),
    ))
    if effect.op == "set":
        return (
            not has_call_metadata
            and bool(effect.target)
            and bool(effect.value)
            and effect.from_source is not None
        )
    if effect.op == "ret":
        return not has_call_metadata and effect.target is None and (
            (bool(effect.value) and effect.from_source is not None)
            or (effect.value is None and effect.from_source is None)
        )
    if effect.op == "call":
        return (
            bool(effect.occurrence_id)
            and bool(effect.call_source)
            and bool(effect.target)
            and effect.value is None
            and effect.from_source is None
        )
    if effect.op in {"jump", "select", "insert", "update", "delete", "upsert"}:
        if has_call_metadata:
            return False
        return bool(effect.target) and effect.value is None and effect.from_source is None
    if effect.op in {"raise_error", "exit_loop", "commit", "rollback"}:
        return (
            not has_call_metadata
            and effect.target is None
            and effect.value is None
            and effect.from_source is None
        )
    return False
