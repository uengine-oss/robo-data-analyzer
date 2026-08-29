"""Immutable pre-publication completeness and performance product."""
from __future__ import annotations

import re

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
)


ANALYSIS_HEALTH_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
    "missing_total",
    "duplicate_total",
    "unresolved_call_count",
    "unresolved_data_object_count",
    "prepublish_wall_time_ms",
    "prepublish_performance_artifact_hash",
})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AnalysisHealthProjectionError(ValueError):
    """The immutable pre-publication ledger is incomplete or not clean."""


def _nonnegative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnalysisHealthProjectionError(f"{name} must be a non-negative integer")
    return value


def build_analysis_health_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    missing_total: int,
    duplicate_total: int,
    unresolved_call_count: int,
    unresolved_data_object_count: int,
    prepublish_wall_time_ms: int,
    prepublish_performance_artifact_hash: str,
) -> ProductWriteBatch:
    """Create the single immutable health record required before publication."""
    missing = _nonnegative_integer("missing_total", missing_total)
    duplicates = _nonnegative_integer("duplicate_total", duplicate_total)
    if missing or duplicates:
        raise AnalysisHealthProjectionError(
            "a generation with missing or duplicate required records cannot publish"
        )
    unresolved_calls = _nonnegative_integer(
        "unresolved_call_count", unresolved_call_count,
    )
    unresolved_data_objects = _nonnegative_integer(
        "unresolved_data_object_count", unresolved_data_object_count,
    )
    wall_time = _nonnegative_integer(
        "prepublish_wall_time_ms", prepublish_wall_time_ms,
    )
    if (
        not isinstance(prepublish_performance_artifact_hash, str)
        or _SHA256.fullmatch(prepublish_performance_artifact_hash) is None
    ):
        raise AnalysisHealthProjectionError(
            "prepublish_performance_artifact_hash must be a lowercase SHA-256"
        )

    stable_id = f"{generation_id}::health"
    identity = NodeIdentity("AnalysisHealth", stable_id)
    properties = {
        "stable_id": stable_id,
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
        "missing_total": missing,
        "duplicate_total": duplicates,
        "unresolved_call_count": unresolved_calls,
        "unresolved_data_object_count": unresolved_data_objects,
        "prepublish_wall_time_ms": wall_time,
        "prepublish_performance_artifact_hash": (
            prepublish_performance_artifact_hash
        ),
    }
    return ProductWriteBatch.build(
        stage_name="analysis_health",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=[NodeCreateOperation.from_properties(
            identity,
            properties,
        )],
        expectations=[ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=ANALYSIS_HEALTH_PROPERTY_KEYS,
            present_properties=properties,
        )],
    )
