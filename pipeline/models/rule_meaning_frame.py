"""Strategy-B boundary: exact structural atoms in, bounded meanings out.

Parser and deterministic Analyzer stages own source coordinates, condition polarity,
effect identity, row ownership, and Rule/Example completeness.  The model sees a
compact projection of those facts and may fill only one meaning string for each exact
effect key and one bounded disposition string for each exact row. Conditions remain
Analyzer-owned Python structure: asking the model to paraphrase them duplicates an
exact fact and can invent an unsupported polarity for opaque framework predicates.

This module deliberately does not call an LLM.  It defines the provider wire,
noise-free prompt packet, and deterministic Python projection used by both
Framework and DBMS analysis paths.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.models.analysis_context import ReferencedSymbolContext
from pipeline.models.rule_materialization import validate_structural_effect


RuleRowDispositionValue = Literal[
    "business_rule", "implementation_detail", "insufficient",
]
RuleProjectionStatus = Literal[
    "sufficient", "partial", "insufficient", "not_business",
]


def rule_meaning_response_model(
    condition_keys: Iterable[str],
    effect_keys: Iterable[str],
    row_keys: Iterable[str],
) -> type[BaseModel]:
    """Build an exact-key response schema; missing/extra atoms are invalid."""
    conditions = _validated_keys(condition_keys, "C", "condition")
    effects = _validated_keys(effect_keys, "E", "effect")
    rows = _validated_keys(row_keys, "R", "row")
    if not conditions and not effects:
        raise ValueError("Rule meaning requires at least one condition or effect atom")
    digest = hashlib.sha256(
        (
            "\0".join(conditions)
            + "\1" + "\0".join(effects)
            + "\2" + "\0".join(rows)
        ).encode("utf-8")
    ).hexdigest()[:12]
    effect_model = create_model(
        f"ExactRuleEffectMeanings_{digest}",
        __config__=ConfigDict(extra="forbid"),
        **{key: (str, Field(...)) for key in effects},
    )
    row_model = create_model(
        f"ExactRuleRowDispositions_{digest}",
        __config__=ConfigDict(extra="forbid"),
        **{key: (RuleRowDispositionValue, Field(...)) for key in rows},
    )
    model = create_model(
        f"RuleMeaningResponse_{digest}",
        __config__=ConfigDict(extra="forbid"),
        effects=(effect_model, Field(...)),
        rows=(row_model, Field(...)),
    )
    model.__effect_keys__ = effects
    model.__row_keys__ = rows
    return model


def batch_rule_meaning_response_model(
    assignments: Mapping[str, Mapping[str, object]],
) -> type[BaseModel]:
    """Build one exact DBMS batch schema without echo/list identity fields."""
    if not assignments:
        raise ValueError("Rule meaning batch assignments must be non-empty")
    node_models: dict[str, tuple[type[BaseModel], Field]] = {}
    for node_key, evidence in assignments.items():
        if not str(node_key):
            raise ValueError("Rule meaning batch node key must be non-empty")
        conditions, effects, _rows = _evidence_maps(evidence)
        node_models[str(node_key)] = (
            rule_meaning_response_model(conditions, effects, _rows),
            Field(...),
        )
    if len(node_models) != len(assignments):
        raise ValueError("Rule meaning batch node keys must be unique")
    digest = hashlib.sha256(
        "\0".join(node_models).encode("utf-8")
    ).hexdigest()[:12]
    nodes_model = create_model(
        f"ExactRuleMeaningNodes_{digest}",
        __config__=ConfigDict(extra="forbid"),
        **node_models,
    )
    return create_model(
        f"BatchRuleMeaningResponse_{digest}",
        __config__=ConfigDict(extra="forbid"),
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


def build_rule_meaning_packet(
    evidence: Mapping[str, object],
    *,
    numbered_source: str,
    referenced_symbols: Sequence[ReferencedSymbolContext] = (),
    callee_outcomes_by_id: Mapping[str, Mapping[str, object]] | None = None,
    table_descriptions: Mapping[str, Mapping[str, object]] | None = None,
    api_contracts: Sequence[object] = (),
) -> dict[str, object]:
    """Project only facts that can change an atom's meaning.

    Internal occurrence IDs, source ranges, resolution diagnostics, file paths,
    hashes, graph IDs, and repeated active/effect status are intentionally absent.
    Resolved callee/API/table context is attached to the effect that consumes it so
    the model never joins private IDs or searches an unrelated context list.
    """
    conditions, effects, rows = _evidence_maps(evidence)
    callee_outcomes = callee_outcomes_by_id or {}
    tables = table_descriptions or {}
    contracts = _api_contracts_by_name(api_contracts)
    compact_effects = {
        ref: _project_effect(
            payload,
            callee_outcomes=callee_outcomes,
            table_descriptions=tables,
            api_contracts=contracts,
        )
        for ref, payload in effects.items()
    }
    compact_rows = {
        key: _project_row(row)
        for key, row in rows.items()
    }
    return {
        "source": numbered_source,
        "used_symbols": [
            projected
            for symbol in referenced_symbols
            if (projected := _project_symbol(symbol)) is not None
        ],
        "conditions": dict(conditions),
        "effects": compact_effects,
        "rows": compact_rows,
    }


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
    callee_outcomes: Mapping[str, Mapping[str, object]],
    table_descriptions: Mapping[str, Mapping[str, object]],
    api_contracts: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    op = str(payload.get("op") or "")
    result: dict[str, object] = {"kind": op}
    if op == "call":
        target = str(payload.get("target") or "")
        result["callee"] = target
        call_source = str(payload.get("call_source") or "").strip()
        if call_source:
            result["source"] = call_source
        callee_id = str(payload.get("callee_id") or "")
        outcome = callee_outcomes.get(callee_id)
        if isinstance(outcome, Mapping):
            meaning = str(outcome.get("meaning") or "").strip()
            if meaning:
                result["callee_outcome"] = meaning
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


def _project_row(row: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "kind": str(row.get("role") or ""),
        "when": [str(value) for value in row.get("when") or ()],
        "effects": [str(value) for value in row.get("direct") or ()],
    }
    for source_key, output_key in (
        ("after_control", "after_control"),
        ("at_label", "at_label"),
        ("post_condition", "post_condition"),
    ):
        value = row.get(source_key)
        if value is not None and str(value).strip():
            result[output_key] = str(value)
    children = row.get("nested_children") or ()
    if children:
        result["nested_controls"] = [
            {
                key: child[key]
                for key in ("type", "start_line", "end_line", "condition", "condition_timing")
                if isinstance(child, Mapping) and child.get(key) not in (None, "")
            }
            for child in children
            if isinstance(child, Mapping)
        ]
    return result


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
    """One row's deterministic Strategy-B Python meaning."""

    row_key: str
    anchor_line: int
    effects: tuple[str, ...]
    disposition: RuleRowDispositionValue
    status: RuleProjectionStatus


def project_rule_meanings(
    output: BaseModel,
    evidence: Mapping[str, object],
) -> tuple[RuleMeaningProjection, ...]:
    """Combine model-owned atom text with Analyzer-owned structure exactly once."""
    condition_sources, effect_sources, rows = _evidence_maps(evidence)
    effects = {
        key: str(value).strip()
        for key, value in output.effects.model_dump().items()
    }
    dispositions = {
        key: str(value)
        for key, value in output.rows.model_dump().items()
    }
    if tuple(effects) != tuple(effect_sources):
        raise ValueError("Rule effect meaning coverage mismatch")
    if tuple(dispositions) != tuple(rows):
        raise ValueError("Rule row disposition coverage mismatch")

    projected: list[RuleMeaningProjection] = []
    for row_key, row in rows.items():
        disposition = dispositions[row_key]
        if disposition != "business_rule":
            projected.append(RuleMeaningProjection(
                row_key=row_key,
                anchor_line=int(row_key[1:]),
                effects=(),
                disposition=disposition,
                status=(
                    "not_business"
                    if disposition == "implementation_detail"
                    else "insufficient"
                ),
            ))
            continue
        row_leaves: list[str] = []
        for signed_ref in row.get("when") or ():
            signed_ref = str(signed_ref)
            negated = signed_ref.startswith("!")
            ref = signed_ref[1:] if negated else signed_ref
            if ref not in condition_sources:
                raise ValueError(f"{row_key}: unknown Rule condition ref {signed_ref!r}")
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
            rendered_effects.append(_render_meaning_effect(payload, leaf))
        status = _combined_status(row_leaves)
        projected.append(RuleMeaningProjection(
            row_key=row_key,
            anchor_line=int(row_key[1:]),
            effects=tuple(rendered_effects),
            disposition=disposition,
            status=status,
        ))
    return tuple(projected)


def _combined_status(leaves: Sequence[str]) -> Literal["sufficient", "insufficient"]:
    if not leaves or any(not leaf for leaf in leaves):
        return "insufficient"
    return "sufficient"


def _quoted(*values: object) -> str:
    return ", ".join(json.dumps(str(value), ensure_ascii=False) for value in values)


def _render_meaning_effect(payload: Mapping[str, object], meaning: str) -> str:
    op = str(payload.get("op") or "")
    target = payload.get("target")
    value = payload.get("value")
    if op == "call":
        receiver = payload.get("receiver_source")
        arguments = payload.get("argument_sources") or ()
        receiver_expr = "None" if receiver is None else f"F({_quoted(receiver)})"
        args_expr = ", ".join(f"F({_quoted(argument)})" for argument in arguments)
        return (
            f"meaning_call({_quoted(target)}, {receiver_expr}, [{args_expr}], "
            f"{_quoted(meaning)})"
        )
    if op == "set":
        return f"meaning_set(F({_quoted(target)}), F({_quoted(value)}), {_quoted(meaning)})"
    if op == "ret":
        result = "None" if value is None else f"F({_quoted(value)})"
        return f"meaning_return({result}, {_quoted(meaning)})"
    if op in {"select", "insert", "update", "delete", "upsert"}:
        return f"meaning_{op}({_quoted(target, meaning)})"
    if target is not None:
        return f"meaning_effect({_quoted(op, target, meaning)})"
    return f"meaning_effect({_quoted(op, meaning)})"
