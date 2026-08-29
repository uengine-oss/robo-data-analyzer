"""Patch access-specific Analyzer meaning onto existing READS/WRITES products."""
from __future__ import annotations

from typing import Iterable

from graph.product_write_batch import (
    NodeIdentity,
    ProductRecordExpectation,
    RelationshipIdentity,
    RelationshipPropertySetOperation,
)
from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import ROUTINE_LABELS
from pipeline.models.function_table_usage import FunctionTableUsage


class FunctionTableUsagesProjectionError(ValueError):
    """FunctionTableUsage results disagree with direct data-access structure."""


FUNCTION_TABLE_USAGE_PROPERTY_KEYS = frozenset({
    "used_column_ids",
    "function_table_usage_summary",
    "meaning_status",
    "missing_context_count",
    "missing_context_artifact_hash",
})


def function_table_usage_patch_records(
    *,
    code_nodes: Iterable[CodeNode],
    reads_writes_edges: Iterable[ReadsWrites],
) -> tuple[
    tuple[RelationshipPropertySetOperation, ...],
    tuple[ProductRecordExpectation, ...],
]:
    """Return relationship patches owned by the single function-meaning stage."""
    nodes = list(code_nodes)
    ids = [str(node.id or "").strip() for node in nodes]
    if any(not stable_id for stable_id in ids) or len(ids) != len(set(ids)):
        raise FunctionTableUsagesProjectionError(
            "CodeNode IDs must be unique and non-blank"
        )
    routine_ids = {node.id for node in nodes if node.label in ROUTINE_LABELS}

    direct_access_keys: set[tuple[str, str, str]] = set()
    for edge in reads_writes_edges:
        relation = str(edge.relation or "").upper()
        if relation not in {"READS", "WRITES"}:
            raise FunctionTableUsagesProjectionError(
                f"invalid direct data-access relation: {relation!r}"
            )
        if edge.source_id not in routine_ids:
            raise FunctionTableUsagesProjectionError(
                f"data-access meaning owner is not a routine: {edge.source_id!r}"
            )
        key = relation, edge.source_id, edge.target_id
        direct_access_keys.add(key)

    usage_by_key: dict[tuple[str, str, str], FunctionTableUsage] = {}
    for node in nodes:
        raw_usages = (node.extra or {}).get("function_table_usages") or ()
        for raw_usage in raw_usages:
            try:
                usage = (
                    raw_usage
                    if isinstance(raw_usage, FunctionTableUsage)
                    else FunctionTableUsage.model_validate(raw_usage)
                )
            except ValueError as error:
                raise FunctionTableUsagesProjectionError(str(error)) from error
            if usage.owner_function_id != node.id:
                raise FunctionTableUsagesProjectionError(
                    "FunctionTableUsage owner differs from containing CodeNode: "
                    f"{usage.owner_function_id!r} != {node.id!r}"
                )
            relation = "READS" if usage.access_mode == "read" else "WRITES"
            key = relation, usage.owner_function_id, usage.physical_table_id
            if key in usage_by_key:
                raise FunctionTableUsagesProjectionError(
                    f"duplicate FunctionTableUsage result: {key!r}"
                )
            usage_by_key[key] = usage

    if set(usage_by_key) != direct_access_keys:
        missing = sorted(direct_access_keys - set(usage_by_key))
        unexpected = sorted(set(usage_by_key) - direct_access_keys)
        raise FunctionTableUsagesProjectionError(
            "FunctionTableUsage population differs from direct relationships: "
            f"missing={missing}, unexpected={unexpected}"
        )

    patches: list[RelationshipPropertySetOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for key, usage in sorted(usage_by_key.items()):
        relation, function_id, table_id = key
        properties: dict[str, object] = {
            "used_column_ids": usage.used_column_ids,
            "meaning_status": usage.meaning_status,
            "missing_context_count": usage.missing_context_count,
        }
        if usage.function_table_usage_summary:
            properties["function_table_usage_summary"] = (
                usage.function_table_usage_summary
            )
        if usage.missing_context_artifact_hash:
            properties["missing_context_artifact_hash"] = (
                usage.missing_context_artifact_hash
            )
        identity = RelationshipIdentity(
            relation,
            NodeIdentity("CodeNode", function_id),
            NodeIdentity("Table", table_id),
        )
        patches.extend(
            RelationshipPropertySetOperation.from_value(identity, name, value)
            for name, value in sorted(properties.items())
        )
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=FUNCTION_TABLE_USAGE_PROPERTY_KEYS,
            present_properties=properties,
        ))

    return tuple(patches), tuple(expectations)
