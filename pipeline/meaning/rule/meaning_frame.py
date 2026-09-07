"""Strategy-B boundary: exact structural atoms in, bounded meanings out.

Parser and deterministic Analyzer stages own source coordinates, condition polarity,
effect identity, row ownership, and Rule completeness.  The model sees a
compact projection of those facts and may fill only one plain-language meaning for
each exact condition/effect key and one bounded disposition for each exact row.
Analyzer retains condition polarity and composes the row description itself.

This module deliberately does not call an LLM.  It defines the provider wire,
noise-free prompt packet, and deterministic Python projection used by both
Framework and DBMS analysis paths.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from pipeline.models.analysis_context import ReferencedSymbolContext
from llm.structured_schema import CompactLlmResponse
from llm.configs.config_loader import load_llm_config
from pipeline.models.rule_materialization import validate_structural_effect
from shared.config.app_settings import SETTINGS


RuleRowDispositionValue = Literal[
    "business_rule", "implementation_detail", "insufficient",
]
RuleProjectionStatus = Literal[
    "sufficient", "partial", "insufficient", "not_business", "unavailable",
]

@lru_cache(maxsize=8)
def _configured_rule_effect_limit(config_name: str) -> int | None:
    return load_llm_config(config_name).max_rule_effects_per_request


def configured_rule_effect_limit() -> int | None:
    """Return the deployment profile's measured Rule response limit, if any."""
    return _configured_rule_effect_limit(SETTINGS.analysis_llm.config)


def rule_meaning_response_model(
    condition_keys: Iterable[str],
    effect_keys: Iterable[str],
    row_keys: Iterable[str],
) -> type[BaseModel]:
    """Build an exact-key response schema; missing/extra atoms are invalid."""
    conditions = _validated_keys(condition_keys, "C", "condition")
    effects = _validated_keys(effect_keys, "E", "effect")
    rows = _validated_keys(row_keys, "R", "row")
    _require_consecutive_wire_keys(conditions, "C", "condition")
    _require_consecutive_wire_keys(effects, "E", "effect")
    _require_consecutive_wire_keys(rows, "R", "row")
    if not conditions and not effects:
        raise ValueError("Rule meaning requires at least one condition or effect atom")
    effect_model = create_model(
        f"RuleEffectMeanings_{len(effects)}",
        __base__=CompactLlmResponse,
        **{key: (str, Field(...)) for key in effects},
    )
    row_model = create_model(
        f"RuleRowDispositions_{len(rows)}",
        __base__=CompactLlmResponse,
        **{key: (RuleRowDispositionValue, Field(...)) for key in rows},
    )
    model = create_model(
        f"RuleMeaningResponse_{len(conditions)}_{len(effects)}_{len(rows)}",
        __base__=CompactLlmResponse,
        conditions=(create_model(
            f"RuleConditionMeanings_{len(conditions)}",
            __base__=CompactLlmResponse,
            **{key: (str, Field(...)) for key in conditions},
        ), Field(...)),
        effects=(effect_model, Field(...)),
        rows=(row_model, Field(...)),
    )
    model.__effect_keys__ = effects
    model.__condition_keys__ = conditions
    model.__row_keys__ = rows
    return model


def batch_rule_meaning_response_model(
    assignments: Mapping[str, Mapping[str, object]],
) -> type[BaseModel]:
    """Build one exact DBMS batch schema without echo/list identity fields."""
    if not assignments:
        raise ValueError("Rule meaning batch assignments must be non-empty")
    expected_node_keys = tuple(
        f"N{index}" for index in range(1, len(assignments) + 1)
    )
    if tuple(assignments) != expected_node_keys:
        raise ValueError("Rule meaning batch node keys must be consecutive ordinals")
    node_models: dict[str, tuple[type[BaseModel], Field]] = {}
    for node_key, evidence in assignments.items():
        if not str(node_key):
            raise ValueError("Rule meaning batch node key must be non-empty")
        conditions, effects, rows = rule_meaning_wire_keys(evidence)
        node_models[str(node_key)] = (
            rule_meaning_response_model(conditions, effects, rows),
            Field(...),
        )
    if len(node_models) != len(assignments):
        raise ValueError("Rule meaning batch node keys must be unique")
    nodes_model = create_model(
        f"RuleMeaningNodes_{len(node_models)}",
        __base__=CompactLlmResponse,
        **node_models,
    )
    return create_model(
        "BatchRuleMeaningResponse",
        __base__=CompactLlmResponse,
        nodes=(nodes_model, Field(...)),
    )


def project_batch_rule_meanings(
    output: BaseModel,
    assignments: Mapping[str, Mapping[str, object]],
) -> dict[str, tuple["RuleMeaningProjection", ...]]:
    """Project every exact batch node and reject missing/extra node keys."""
    raw_nodes = output.nodes.model_dump()
    if tuple(raw_nodes) != tuple(assignments):
        raise ValueError("Rule meaning batch node coverage mismatch")
    result: dict[str, tuple[RuleMeaningProjection, ...]] = {}
    for node_key, evidence in assignments.items():
        node_output = getattr(output.nodes, node_key)
        result[node_key] = project_rule_meanings(node_output, evidence)
    return result


def _validated_keys(values: Iterable[str], prefix: str, name: str) -> tuple[str, ...]:
    keys = tuple(str(value) for value in values)
    if (
        len(keys) != len(set(keys))
        or any(not key.startswith(prefix) or not key[1:].isdigit() for key in keys)
    ):
        raise ValueError(f"invalid Rule {name} keys: {keys!r}")
    return keys


def _require_consecutive_wire_keys(
    keys: tuple[str, ...], prefix: str, name: str,
) -> None:
    expected = tuple(f"{prefix}{index}" for index in range(1, len(keys) + 1))
    if keys != expected:
        raise ValueError(
            f"Rule {name} wire keys must be consecutive request ordinals: {keys!r}"
        )


def _wire_key_map(keys: Iterable[str], prefix: str) -> dict[str, str]:
    return {
        key: f"{prefix}{index}"
        for index, key in enumerate(keys, start=1)
    }


def rule_meaning_wire_keys(
    evidence: Mapping[str, object],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return compact request-local keys without exposing internal row coordinates."""
    conditions, effects, rows = _evidence_maps(evidence)
    return (
        tuple(_wire_key_map(conditions, "C").values()),
        tuple(_wire_key_map(effects, "E").values()),
        tuple(_wire_key_map(rows, "R").values()),
    )


def build_rule_meaning_packet(
    evidence: Mapping[str, object],
    *,
    referenced_symbols: Sequence[ReferencedSymbolContext] = (),
    callee_effects_by_id: Mapping[str, Mapping[str, object]] | None = None,
    table_descriptions: Mapping[str, Mapping[str, object]] | None = None,
    api_contracts: Sequence[object] = (),
) -> dict[str, object]:
    """Project only facts that can change an atom's meaning.

    Internal occurrence IDs, source ranges, resolution diagnostics, file paths,
    hashes, graph IDs, and repeated active/effect status are intentionally absent.
    Each call keeps its exact source statement; the old packet discarded arguments
    and forced the model to align repeated callees with a large row source by order.
    Resolved callee/API/table context remains attached to the consuming effect.
    """
    conditions, effects, rows = _evidence_maps(evidence)
    condition_keys = _wire_key_map(conditions, "C")
    effect_keys = _wire_key_map(effects, "E")
    row_keys = _wire_key_map(rows, "R")
    callee_effects = callee_effects_by_id or {}
    tables = table_descriptions or {}
    contracts = _api_contracts_by_name(api_contracts)
    compact_effects = {
        effect_keys[ref]: _project_effect(
            payload,
            callee_effects=callee_effects,
            table_descriptions=tables,
            api_contracts=contracts,
        )
        for ref, payload in effects.items()
    }
    compact_rows = {
        row_keys[key]: _project_row(
            row,
            anchor_line=int(key[1:]),
            condition_keys=condition_keys,
            effect_keys=effect_keys,
        )
        for key, row in rows.items()
    }
    packet: dict[str, object] = {
        "effects": compact_effects,
        "rows": compact_rows,
    }
    projected_symbols = [
        projected
        for symbol in referenced_symbols
        if (projected := _project_symbol(symbol)) is not None
    ]
    if projected_symbols:
        packet["used_symbols"] = projected_symbols
    if conditions:
        packet["conditions"] = {
            condition_keys[key]: value
            for key, value in conditions.items()
        }
    return packet


def _evidence_maps(
    evidence: Mapping[str, object],
) -> tuple[
    Mapping[str, str],
    Mapping[str, Mapping[str, object]],
    Mapping[str, Mapping[str, object]],
]:
    conditions = evidence.get("conditions")
    effects = evidence.get("effects")
    rows = evidence.get("rows")
    if not isinstance(conditions, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in conditions.items()
    ):
        raise ValueError("Rule meaning evidence requires a string condition map")
    if not isinstance(effects, Mapping) or not all(
        isinstance(key, str) and isinstance(value, Mapping)
        for key, value in effects.items()
    ):
        raise ValueError("Rule meaning evidence requires an object effect map")
    if not isinstance(rows, Mapping) or not all(
        isinstance(key, str) and isinstance(value, Mapping)
        for key, value in rows.items()
    ):
        raise ValueError("Rule meaning evidence requires an object row map")
    _validated_keys(conditions, "C", "condition")
    _validated_keys(effects, "E", "effect")
    for payload in effects.values():
        validate_structural_effect(payload)
    row_keys = tuple(rows)
    if (
        len(row_keys) != len(set(row_keys))
        or any(not key.startswith("R") or not key[1:].isdigit() for key in row_keys)
    ):
        raise ValueError(f"invalid Rule row keys: {row_keys!r}")
    return conditions, effects, rows


def _project_symbol(symbol: ReferencedSymbolContext) -> dict[str, object] | None:
    value: dict[str, object] = {
        "name": symbol.name,
        "kind": symbol.kind,
    }
    for key, item in (
        ("type", symbol.data_type),
        ("declared_value", symbol.declared_value),
        ("value_meaning", symbol.value_semantics),
    ):
        if str(item).strip():
            value[key] = item
    if symbol.initialization_sources:
        value["initialization_sources"] = list(symbol.initialization_sources)
    # A name and kind alone are recoverable from source/structure and add no value to
    # Rule meaning.  Keep the symbol only when Analyzer has extra type/value context.
    return value if len(value) > 2 else None


def _project_effect(
    payload: Mapping[str, object],
    *,
    callee_effects: Mapping[str, Mapping[str, object]],
    table_descriptions: Mapping[str, Mapping[str, object]],
    api_contracts: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    op = str(payload.get("op") or "")
    result: dict[str, object] = {"kind": op}
    if op == "call":
        target = str(payload.get("target") or "")
        call_source = str(payload.get("call_source") or "").strip()
        if call_source:
            result["source"] = call_source
        else:
            result["callee"] = target
        callee_id = str(payload.get("callee_id") or "")
        effects = callee_effects.get(callee_id)
        if isinstance(effects, Mapping):
            visible = {
                key: value for key, value in effects.items()
                if key != "callee" and value not in (None, "", (), [], {})
            }
            if visible:
                result["callee_effects"] = visible
        contract = api_contracts.get(target)
        if contract is not None:
            result["api_contract"] = dict(contract)
        return result
    target = payload.get("target")
    value = payload.get("value")
    if target is not None and str(target).strip():
        result["target"] = str(target)
    if value is not None and str(value).strip():
        result["value"] = str(value)
    if op in {"select", "insert", "update", "delete", "upsert"}:
        table = str(target or "")
        table_context = table_descriptions.get(table)
        if isinstance(table_context, Mapping):
            meaning = str(table_context.get("description") or "").strip()
            if meaning:
                result["table_meaning"] = meaning
    return result


def _project_row(
    row: Mapping[str, object],
    *,
    anchor_line: int,
    condition_keys: Mapping[str, str],
    effect_keys: Mapping[str, str],
) -> dict[str, object]:
    when = []
    for raw_value in row.get("when") or ():
        value = str(raw_value)
        negated = value.startswith("!")
        internal_key = value[1:] if negated else value
        wire_key = condition_keys[internal_key]
        when.append(f"!{wire_key}" if negated else wire_key)
    result: dict[str, object] = {
        "line": anchor_line,
        "kind": str(row.get("role") or ""),
        "when": when,
        "effects": [effect_keys[str(value)] for value in row.get("direct") or ()],
    }
    if str(row.get("after_control") or "").strip():
        result["after_control"] = True
    if str(row.get("at_label") or "").strip():
        result["at_label"] = True
    post_condition = row.get("post_condition")
    if post_condition is not None and str(post_condition).strip():
        result["post_condition"] = str(post_condition)
    return result


def partition_rule_meaning_evidence(
    evidence: Mapping[str, object],
    *,
    max_effects: int | None,
) -> tuple[dict[str, object], ...]:
    """Partition whole rows by response obligations without retry or loss."""
    if max_effects is not None and (
        isinstance(max_effects, bool) or max_effects < 1
    ):
        raise ValueError("Rule meaning max_effects must be a positive integer")
    conditions, effects, rows = _evidence_maps(evidence)
    if not rows:
        return ()

    grouped_rows: list[list[tuple[str, Mapping[str, object]]]] = []
    current: list[tuple[str, Mapping[str, object]]] = []
    current_effects: set[str] = set()
    for row_key, row in rows.items():
        row_effects = {str(value) for value in row.get("direct") or ()}
        if max_effects is not None and len(row_effects) > max_effects:
            raise ValueError(
                f"{row_key}: one Rule row exceeds the atomic effect budget: "
                f"{len(row_effects)} > {max_effects}"
            )
        if (
            max_effects is not None
            and current
            and len(current_effects | row_effects) > max_effects
        ):
            grouped_rows.append(current)
            current = []
            current_effects = set()
        current.append((row_key, row))
        current_effects.update(row_effects)
    if current:
        grouped_rows.append(current)

    partitions: list[dict[str, object]] = []
    for group in grouped_rows:
        selected_rows = dict(group)
        condition_refs = {
            str(value).lstrip("!")
            for row in selected_rows.values()
            for value in row.get("when") or ()
        }
        effect_refs = {
            str(value)
            for row in selected_rows.values()
            for value in row.get("direct") or ()
        }
        partitions.append({
            "conditions": {
                key: value for key, value in conditions.items()
                if key in condition_refs
            },
            "effects": {
                key: value for key, value in effects.items()
                if key in effect_refs
            },
            "rows": selected_rows,
        })
    return tuple(partitions)


def _api_contracts_by_name(values: Sequence[object]) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for value in values:
        if isinstance(value, BaseModel):
            payload = value.model_dump(exclude_none=True)
        elif isinstance(value, Mapping):
            payload = dict(value)
        else:
            continue
        name = str(payload.pop("name", "") or "")
        if name:
            result[name] = payload
    return result


@dataclass(frozen=True)
class RuleMeaningProjection:
    """One row's verified structure and plain-language display meaning."""

    row_key: str
    anchor_line: int
    condition_description: str
    effect_descriptions: tuple[str, ...]
    disposition: RuleRowDispositionValue
    status: RuleProjectionStatus
    reason: str = ""


@dataclass(frozen=True)
class RuleMeaningAssignment:
    """Model-owned rows plus Analyzer-owned parent-control exclusions."""

    model_evidence: dict[str, object]
    deterministic_projections: tuple[RuleMeaningProjection, ...]


def prepare_rule_meaning_assignment(
    evidence: Mapping[str, object],
) -> RuleMeaningAssignment:
    """Remove parent-only control rows from the model decision boundary.

    A row with no direct effect cannot publish a standalone Rule. Its condition
    remains in effect-owning child rows' complete ``when`` path, so asking a model
    to classify the empty row adds cost and can create an invalid product Rule.
    """
    conditions, effects, rows = _evidence_maps(evidence)
    model_rows: dict[str, Mapping[str, object]] = {}
    deterministic: list[RuleMeaningProjection] = []
    for row_key, row in rows.items():
        if not tuple(row.get("direct") or ()):
            deterministic.append(RuleMeaningProjection(
                row_key=row_key,
                anchor_line=int(row_key[1:]),
                condition_description="",
                effect_descriptions=(),
                disposition="implementation_detail",
                status="not_business",
            ))
        else:
            model_rows[row_key] = row

    condition_refs = {
        str(reference).lstrip("!")
        for row in model_rows.values()
        for reference in row.get("when") or ()
    }
    effect_refs = {
        str(reference)
        for row in model_rows.values()
        for reference in row.get("direct") or ()
    }
    unknown_conditions = condition_refs - set(conditions)
    unknown_effects = effect_refs - set(effects)
    if unknown_conditions or unknown_effects:
        raise ValueError(
            "Rule meaning assignment references unknown atoms: "
            f"conditions={sorted(unknown_conditions)} effects={sorted(unknown_effects)}"
        )
    return RuleMeaningAssignment(
        model_evidence={
            "conditions": {
                key: value for key, value in conditions.items()
                if key in condition_refs
            },
            "effects": {
                key: value for key, value in effects.items()
                if key in effect_refs
            },
            "rows": model_rows,
        },
        deterministic_projections=tuple(deterministic),
    )


def unavailable_rule_meaning_projections(
    evidence: Mapping[str, object],
    *,
    reason: str,
) -> tuple[RuleMeaningProjection, ...]:
    """Account only model-owned rows when their one semantic call is unavailable."""
    _conditions, _effects, rows = _evidence_maps(evidence)
    concrete_reason = " ".join(str(reason).split())
    if not concrete_reason:
        raise ValueError("unavailable Rule meaning requires a concrete reason")
    return tuple(
        RuleMeaningProjection(
            row_key=row_key,
            anchor_line=int(row_key[1:]),
            condition_description="",
            effect_descriptions=(),
            disposition="insufficient",
            status="unavailable",
            reason=concrete_reason,
        )
        for row_key in rows
    )


def project_rule_meanings(
    output: BaseModel,
    evidence: Mapping[str, object],
) -> tuple[RuleMeaningProjection, ...]:
    """Combine model-owned atom text with Analyzer-owned structure exactly once."""
    condition_sources, effect_sources, rows = _evidence_maps(evidence)
    condition_keys = _wire_key_map(condition_sources, "C")
    effect_keys = _wire_key_map(effect_sources, "E")
    row_keys = _wire_key_map(rows, "R")
    wire_conditions = output.conditions.model_dump()
    wire_effects = output.effects.model_dump()
    wire_dispositions = output.rows.model_dump()
    if tuple(wire_conditions) != tuple(condition_keys.values()):
        raise ValueError("Rule condition meaning coverage mismatch")
    if tuple(wire_effects) != tuple(effect_keys.values()):
        raise ValueError("Rule effect meaning coverage mismatch")
    if tuple(wire_dispositions) != tuple(row_keys.values()):
        raise ValueError("Rule row disposition coverage mismatch")
    conditions = {
        internal_key: str(wire_conditions[wire_key]).strip()
        for internal_key, wire_key in condition_keys.items()
    }
    effects = {
        internal_key: str(wire_effects[wire_key]).strip()
        for internal_key, wire_key in effect_keys.items()
    }
    dispositions = {
        internal_key: str(wire_dispositions[wire_key])
        for internal_key, wire_key in row_keys.items()
    }

    projected: list[RuleMeaningProjection] = []
    for row_key, row in rows.items():
        disposition = dispositions[row_key]
        if disposition != "business_rule":
            projected.append(RuleMeaningProjection(
                row_key=row_key,
                anchor_line=int(row_key[1:]),
                condition_description="",
                effect_descriptions=(),
                disposition=disposition,
                status=(
                    "not_business"
                    if disposition == "implementation_detail"
                    else "insufficient"
                ),
            ))
            continue
        row_leaves: list[str] = []
        condition_descriptions: list[str] = []
        for signed_ref in row.get("when") or ():
            signed_ref = str(signed_ref)
            negated = signed_ref.startswith("!")
            ref = signed_ref[1:] if negated else signed_ref
            if ref not in condition_sources:
                raise ValueError(f"{row_key}: unknown Rule condition ref {signed_ref!r}")
            meaning = conditions.get(ref)
            if meaning is None:
                raise ValueError(f"{row_key}: missing Rule condition meaning {signed_ref!r}")
            row_leaves.append(meaning)
            if meaning:
                condition_descriptions.append(
                    f"({meaning})이 아닌 경우" if negated else meaning
                )
        rendered_effects: list[str] = []
        for raw_ref in row.get("direct") or ():
            ref = str(raw_ref)
            payload = effect_sources.get(ref)
            leaf = effects.get(ref)
            if payload is None or leaf is None:
                raise ValueError(f"{row_key}: unknown Rule effect ref {ref!r}")
            row_leaves.append(leaf)
            if not leaf:
                continue
            rendered_effects.append(leaf)
        status = (
            _combined_status(row_leaves)
            if rendered_effects
            else "insufficient"
        )
        projected.append(RuleMeaningProjection(
            row_key=row_key,
            anchor_line=int(row_key[1:]),
            condition_description=" 그리고 ".join(condition_descriptions),
            effect_descriptions=tuple(rendered_effects),
            disposition=disposition,
            status=status,
        ))
    return tuple(projected)


def _combined_status(leaves: Sequence[str]) -> Literal["sufficient", "insufficient"]:
    if not leaves or any(not leaf for leaf in leaves):
        return "insufficient"
    return "sufficient"
