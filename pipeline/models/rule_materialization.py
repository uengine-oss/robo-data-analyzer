"""Materialize canonical Rule leaves from parser-owned branch evidence.

Rule conditions, effects, coordinates, ownership, and Examples are structural facts.
They must not pass through a model response before graph/API/MCP consumers can use
them.  This module is the single conversion boundary from ``rule_evidence`` to the
legacy ``RuleItem`` projection.
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
    source_line: int | None = None
    call_source: str | None = None
    argument_sources: tuple[str, ...] = ()
    receiver_source: str | None = None
    source_range: tuple[int, int] | None = None
    callee_source: str = ""
    callee_kind: str = ""
    context_role: str = ""
    condition_fact_id: str | None = None
    presence_status: str = ""
    resolution_status: str = ""
    resolution_outcome: str = ""
    resolution_scope: str | None = None
    resolution_reason: str = ""
    callee_id: str = ""
    external_identity_key: str = ""


def materialize_rule_items(
    evidence: Mapping[str, object],
    rows: Sequence[int],
) -> list[RuleItem]:
    """Create exact ``cond``/``then`` Rules with an empty compatibility ``nl``.

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
            cond=render_projected_row_condition(
                _evidence_row(evidence_rows, row), evidence,
            ),
            then=[
                _render_selected_effect(effect_ref, effects)
                for effect_ref in (
                    _evidence_row(evidence_rows, row).get("direct") or ()
                )
            ],
            nl="",
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
    effect = (
        value
        if isinstance(value, StructuralEffect)
        else StructuralEffect.model_validate(value)
    )
    if not _effect_positions_valid(effect):
        raise ValueError(
            "invalid parser-owned Rule effect shape: " + effect.model_dump_json()
        )
    if effect.op == "call":
        return f"call({_quoted(effect.target)})"
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


def _effect_positions_valid(effect: StructuralEffect) -> bool:
    has_call_metadata = any((
        effect.occurrence_id is not None,
        effect.source_line is not None,
        effect.call_source is not None,
        bool(effect.argument_sources),
        effect.receiver_source is not None,
        effect.source_range is not None,
        bool(effect.callee_source),
        bool(effect.callee_kind),
        bool(effect.context_role),
        effect.condition_fact_id is not None,
        bool(effect.presence_status),
        bool(effect.resolution_status),
        bool(effect.resolution_outcome),
        effect.resolution_scope is not None,
        bool(effect.resolution_reason),
        bool(effect.callee_id),
        bool(effect.external_identity_key),
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
        metadata_valid = (
            not has_call_metadata
            or (
                bool(effect.occurrence_id)
                and effect.source_line is not None
                and effect.source_line > 0
                and bool(effect.call_source)
                and effect.context_role in {"", "effect"}
                and effect.condition_fact_id is None
                and effect.presence_status in {"", "active"}
                and (
                    effect.source_range is None
                    or (
                        len(effect.source_range) == 2
                        and effect.source_range[0] >= 0
                        and effect.source_range[1] > 0
                    )
                )
            )
        )
        return (
            metadata_valid
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
