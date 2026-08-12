"""Required-key structured output for direct table insights.

The deterministic R/W linker owns which tables a code node accesses.  The LLM
may describe that use, but it may not add, omit, or reassign table coordinates.
Column identity and coordinates come from the deterministic projection and bypass this
intermediate LLM.  Column meaning is generated once by the final TABLE/COLUMN concern.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from pipeline.models.code_analysis_output import (
    BatchTableInsightsItem,
    BatchTableInsightsOutput,
    ColumnInsight,
    TableInsightItem,
    TableInsightsOutput,
)


def _ordered_tables(
    reads: Iterable[str],
    writes: Iterable[str],
) -> tuple[str, ...]:
    tables = tuple(dict.fromkeys(
        str(table) for table in (*tuple(reads or ()), *tuple(writes or ()))
    ))
    if any(not table for table in tables):
        raise ValueError("direct table IDs must not be empty")
    return tables


def _fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _roles_for(
    table_id: str,
    *,
    read_tables: set[str],
    write_tables: set[str],
    table_ops: Mapping[str, object],
) -> tuple[str, ...]:
    is_read = table_id in read_tables
    is_write = table_id in write_tables
    operation = str(table_ops.get(table_id) or "").strip().upper()
    if operation in {"SELECT", "READ"} and not is_read:
        raise ValueError(f"table op {operation} requires READ ownership: {table_id!r}")
    if operation in {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"} and not is_write:
        raise ValueError(f"table op {operation} requires WRITE ownership: {table_id!r}")
    if operation == "MERGE":
        return ("upsert",)
    if is_read and is_write:
        return ("READ_WRITE",)
    if is_read:
        return ("READ-only", "lookup")
    if operation == "INSERT":
        return ("WRITE-only", "append-only")
    if is_write:
        return ("WRITE-only",)
    raise ValueError(f"table {table_id!r} is not in direct reads/writes")


def _table_map_model(
    reads: Iterable[str],
    writes: Iterable[str],
    table_ops: Mapping[str, object],
    column_assignments: Mapping[str, Mapping[str, object]],
    fingerprint: str,
) -> tuple[type[BaseModel], tuple[str, ...]]:
    reads = tuple(str(value) for value in (reads or ()))
    writes = tuple(str(value) for value in (writes or ()))
    tables = _ordered_tables(reads, writes)
    read_tables = set(reads)
    write_tables = set(writes)
    fields = {}
    for table_id in tables:
        columns = column_assignments.get(table_id, {}) or {}
        if any(not str(column_name) for column_name in columns):
            raise ValueError(f"column assignment name must not be empty: {table_id!r}")
        roles = _roles_for(
            table_id,
            read_tables=read_tables,
            write_tables=write_tables,
            table_ops=table_ops,
        )
        role_type = Literal.__getitem__(roles)
        item_model = create_model(
            f"AssignedTableInsightValue_{fingerprint}_{_fingerprint((table_id, roles))}",
            __config__=ConfigDict(extra="forbid"),
            role=(role_type, Field(...)),
            usage_summary=(str, Field(..., min_length=1)),
        )
        fields[table_id] = (item_model, Field(...))
    return create_model(
        f"AssignedTableInsightMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    ), tables


def table_insights_response_model(
    reads: Iterable[str],
    writes: Iterable[str],
    table_ops: Mapping[str, object] | None = None,
    column_assignments: Mapping[str, Mapping[str, object]] | None = None,
) -> type[BaseModel]:
    """Build one closed, required-key schema for a framework code node."""
    reads = tuple(str(value) for value in (reads or ()))
    writes = tuple(str(value) for value in (writes or ()))
    table_ops = table_ops or {}
    column_assignments = column_assignments or {}
    fingerprint = _fingerprint({
        "reads": reads,
        "writes": writes,
        "table_ops": dict(table_ops),
        "column_assignments": column_assignments,
    })
    table_map, _ = _table_map_model(
        reads, writes, table_ops, column_assignments, fingerprint,
    )
    return create_model(
        f"AssignedTableInsightsOutput_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        table_insights=(table_map, Field(...)),
    )


def normalize_table_insights_response(
    output: BaseModel,
    reads: Iterable[str],
    writes: Iterable[str],
    column_assignments: Mapping[str, Mapping[str, object]] | None = None,
) -> TableInsightsOutput:
    """Convert a validated fixed-key response to the canonical list model."""
    tables = _ordered_tables(reads, writes)
    column_assignments = column_assignments or {}
    return TableInsightsOutput(table_insights=[
        TableInsightItem(
            table=table_id,
            role=getattr(output.table_insights, table_id).role,
            usage_summary=getattr(output.table_insights, table_id).usage_summary,
            columns=[
                ColumnInsight(
                    name=column_name,
                    usage_summary="",
                    ownership=str(evidence["ownership"]),
                    evidence_lines=list(evidence["evidence_lines"]),
                    source_lines=list(evidence["source_lines"]),
                )
                for column_name, evidence in (
                    column_assignments.get(table_id, {}) or {}
                ).items()
            ],
        )
        for table_id in tables
    ])


def batch_table_insights_response_model(
    assignments: Mapping[str, Mapping[str, object]],
) -> type[BaseModel]:
    """Build required DBMS node keys containing required direct-table keys."""
    if not assignments:
        raise ValueError("DBMS table insight assignments must not be empty")
    fingerprint = _fingerprint(assignments)
    node_fields = {}
    for node_key, assignment in assignments.items():
        node_key = str(node_key)
        if not node_key:
            raise ValueError("DBMS node_key must not be empty")
        reads = tuple(str(value) for value in assignment.get("reads", ()) or ())
        writes = tuple(str(value) for value in assignment.get("writes", ()) or ())
        table_ops = assignment.get("table_ops", {}) or {}
        column_assignments = assignment.get("columns", {}) or {}
        table_map, _ = _table_map_model(
            reads,
            writes,
            table_ops,
            column_assignments,
            f"{fingerprint}_{_fingerprint(node_key)}",
        )
        item_model = create_model(
            f"AssignedNodeTableInsights_{fingerprint}_{_fingerprint(node_key)}",
            __config__=ConfigDict(extra="forbid"),
            table_insights=(table_map, Field(...)),
        )
        node_fields[node_key] = (item_model, Field(...))
    nodes_model = create_model(
        f"AssignedNodeTableInsightMap_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        **node_fields,
    )
    return create_model(
        f"AssignedBatchTableInsightsOutput_{fingerprint}",
        __config__=ConfigDict(extra="forbid"),
        nodes=(nodes_model, Field(...)),
    )


def normalize_batch_table_insights_response(
    output: BaseModel,
    assignments: Mapping[str, Mapping[str, object]],
) -> BatchTableInsightsOutput:
    """Convert validated fixed DBMS node/table maps to canonical list models."""
    nodes: list[BatchTableInsightsItem] = []
    for node_key, assignment in assignments.items():
        item = getattr(output.nodes, node_key)
        canonical = normalize_table_insights_response(
            item,
            assignment.get("reads", ()) or (),
            assignment.get("writes", ()) or (),
            assignment.get("columns", {}) or {},
        )
        nodes.append(BatchTableInsightsItem(
            node_key=node_key,
            table_insights=canonical.table_insights,
        ))
    return BatchTableInsightsOutput(nodes=nodes)
