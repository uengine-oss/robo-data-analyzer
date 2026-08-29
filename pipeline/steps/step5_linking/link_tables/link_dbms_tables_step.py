"""Resolve DBMS Parser data-object occurrences into routine-to-table product links.

The Parser owns SQL syntax occurrences and exact source coordinates. This step
owns only physical-table binding against the uploaded DDL/Catalog population and
projection to user-routine READS/WRITES candidates.

There is deliberately no source-text scanner, unresolved graph node, direct
Neo4j write, or silent statement-to-routine promotion here.
"""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Optional

from langchain_core.runnables import RunnableConfig

from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.dml_semantics import dml_semantics
from pipeline.execution.graph_delta_stream import emit_message
from pipeline.execution.step_config import step_config
from pipeline.languages import ROUTINE_LABELS
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.models.sql_identifier import SqlIdentifierKey, sql_identifier_key
from pipeline.steps.step5_linking.link_tables.view_expansion import expand_view_edges
from shared.events.runtime_logger import log_info

_STEP = "step5.link_dbms_tables"


class DbmsDataAccessContractError(ValueError):
    """DBMS Parser evidence cannot be resolved without guessing or loss."""


async def link_dbms_tables_step(
    state: dict,
    config: Optional[RunnableConfig] = None,
) -> dict:
    """Resolve versioned Parser usages and return final routine/table candidates."""
    cfg = step_config(config)
    code_nodes: list[CodeNode] = list(state.get("code_nodes") or ())
    tables: list[Table] = list(state.get("tables") or ())
    if not code_nodes:
        log_info(_STEP, "skip", reason="no code_nodes")
        return {}

    datasource = (getattr(cfg.get("codebase"), "datasource", None) or "").strip()
    started = time.monotonic()
    log_info(_STEP, "start", code_nodes=len(code_nodes), tables=len(tables))

    edges = _link(code_nodes, tables, datasource=datasource)
    view_edges = expand_view_edges(edges, tables)
    if view_edges:
        log_info(_STEP, "view_expansion", added=len(view_edges))
        edges = [*edges, *view_edges]

    emit_message(cfg.get("stream"), f"✅ DBMS 테이블 연결 후보 생성 — {len(edges)}건")
    unresolved = sum(
        len((node.extra or {}).get("unresolved_data_object_usages") or ())
        for node in code_nodes
    )
    log_info(
        _STEP,
        "done",
        reads_writes_edges=len(edges),
        unresolved_occurrences=unresolved,
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"reads_writes_edges": edges}


def _link(
    code_nodes: list[CodeNode],
    tables: list[Table],
    *,
    datasource: str = "",
) -> list[ReadsWrites]:
    """Bind each Parser occurrence once and project resolved product links.

    Parser occurrences remain on their statement CodeNode in data_object_usages.
    Resolved product candidates use the owning routine as source and a physical
    Table as target. Product duplicates are collapsed here while the occurrence
    population remains intact upstream.
    """
    nodes_by_id = _unique_code_nodes(code_nodes)
    tables_by_id = _unique_tables(tables)
    tables_by_name: dict[SqlIdentifierKey, list[Table]] = {}
    for table in tables:
        tables_by_name.setdefault(
            sql_identifier_key(table.name, table.name_quoted),
            [],
        ).append(table)

    semantics = dml_semantics()
    edges_by_product: dict[tuple[str, str, str, str], ReadsWrites] = {}

    for node in code_nodes:
        extra = node.extra or {}
        evidence_version = extra.get("data_object_evidence_version")
        statement_dml_type = semantics.dml_type_of(node.label)

        if evidence_version is None:
            if statement_dml_type:
                raise DbmsDataAccessContractError(
                    "DBMS DML node missing Parser data-object evidence: "
                    f"{node.file_path}:{node.start_line} ({node.id}, {node.label})"
                )
            continue
        try:
            version = int(evidence_version)
        except (TypeError, ValueError) as error:
            raise DbmsDataAccessContractError(
                f"invalid Parser data-object evidence version: {evidence_version!r}"
            ) from error
        if version <= 0:
            raise DbmsDataAccessContractError(
                f"non-positive Parser data-object evidence version: {version}"
            )

        raw_usages = tuple(extra.get("data_object_usages") or ())
        if node.label in ROUTINE_LABELS and raw_usages:
            raise DbmsDataAccessContractError(
                "Parser data-object occurrence must belong to a statement node, "
                f"not a routine container: {node.id!r}"
            )
        routine = _owning_routine(node, nodes_by_id)
        if raw_usages and routine is None:
            raise DbmsDataAccessContractError(
                "DBMS data-object occurrence has no owning user routine: "
                f"{node.file_path}:{node.start_line} ({node.id})"
            )

        resolved_usages: list[DataObjectUsageEvidence] = []
        unresolved_usages: list[DataObjectUsageEvidence] = []
        for raw_usage in raw_usages:
            usage = _validated_usage(node, raw_usage)
            scoped_usage = replace(usage, datasource=datasource)
            table = _resolve_usage(
                scoped_usage,
                tables_by_id=tables_by_id,
                tables_by_name=tables_by_name,
            )
            if table is None:
                resolved_usages.append(scoped_usage)
                unresolved_usages.append(scoped_usage)
                continue

            resolved = scoped_usage.resolved(table.id)
            resolved_usages.append(resolved)
            relation = "READS" if resolved.access == "READ" else "WRITES"
            operation = "SELECT" if relation == "READS" else statement_dml_type
            if relation == "WRITES" and not operation:
                raise DbmsDataAccessContractError(
                    "Parser WRITE occurrence belongs to a node without declared DML "
                    f"semantics: {node.file_path}:{node.start_line} "
                    f"({node.id}, {node.label})"
                )
            assert routine is not None
            key = (routine.id, table.id, relation, operation)
            edges_by_product.setdefault(
                key,
                ReadsWrites(
                    source_id=routine.id,
                    target_id=table.id,
                    relation=relation,
                    dml_type=operation,
                    evidence_file=resolved.evidence_file,
                    evidence_line=resolved.evidence_line,
                ),
            )

        node.extra["data_object_usages"] = tuple(resolved_usages)
        node.extra["unresolved_data_object_usages"] = tuple(unresolved_usages)

    return [edges_by_product[key] for key in sorted(edges_by_product)]


def _unique_code_nodes(code_nodes: list[CodeNode]) -> dict[str, CodeNode]:
    result: dict[str, CodeNode] = {}
    for node in code_nodes:
        stable_id = str(node.id or "").strip()
        if not stable_id or stable_id in result:
            raise DbmsDataAccessContractError(
                f"CodeNode IDs must be unique and non-blank: {stable_id!r}"
            )
        result[stable_id] = node
    return result


def _unique_tables(tables: list[Table]) -> dict[str, Table]:
    result: dict[str, Table] = {}
    for table in tables:
        stable_id = str(table.id or "").strip()
        if not stable_id or stable_id in result:
            raise DbmsDataAccessContractError(
                f"Table IDs must be unique and non-blank: {stable_id!r}"
            )
        result[stable_id] = table
    return result


def _owning_routine(
    node: CodeNode,
    nodes_by_id: dict[str, CodeNode],
) -> CodeNode | None:
    current: CodeNode | None = node
    visited: set[str] = set()
    while current is not None:
        if current.id in visited:
            raise DbmsDataAccessContractError(
                f"CodeNode parent cycle while resolving data access: {current.id!r}"
            )
        visited.add(current.id)
        if current.label in ROUTINE_LABELS:
            return current
        current = nodes_by_id.get(current.parent_id) if current.parent_id else None
    return None


def _validated_usage(
    node: CodeNode,
    raw_usage: object,
) -> DataObjectUsageEvidence:
    if not isinstance(raw_usage, DataObjectUsageEvidence):
        raise DbmsDataAccessContractError(
            f"invalid Parser data-object occurrence type at {node.id!r}: "
            f"{type(raw_usage).__name__}"
        )
    if raw_usage.source_id != node.id:
        raise DbmsDataAccessContractError(
            "Parser data-object occurrence source differs from containing node: "
            f"{raw_usage.source_id!r} != {node.id!r}"
        )
    if not raw_usage.raw_reference or not raw_usage.name:
        raise DbmsDataAccessContractError(
            f"Parser data-object occurrence missing source spelling: {node.id!r}"
        )
    if not raw_usage.evidence_file or raw_usage.evidence_line <= 0:
        raise DbmsDataAccessContractError(
            f"Parser data-object occurrence missing coordinate: {node.id!r}"
        )
    return raw_usage


def _resolve_usage(
    usage: DataObjectUsageEvidence,
    *,
    tables_by_id: dict[str, Table],
    tables_by_name: dict[SqlIdentifierKey, list[Table]],
) -> Table | None:
    if usage.resolution == "exact":
        if not usage.table_id or usage.table_id not in tables_by_id:
            raise DbmsDataAccessContractError(
                "exact data-object resolution points outside the current Table "
                f"population: {usage.table_id!r}"
            )
        return tables_by_id[usage.table_id]
    return _resolve_parser_usage(usage, tables_by_name)


def _resolve_parser_usage(
    usage: DataObjectUsageEvidence,
    tables_by_name: dict[SqlIdentifierKey, list[Table]],
) -> Table | None:
    """Resolve only an exact, unique local DDL identity from Parser structure."""
    if usage.database_link:
        return None
    candidates = list(
        tables_by_name.get(sql_identifier_key(usage.name, usage.name_quoted), ())
    )
    if usage.schema:
        candidates = [
            table
            for table in candidates
            if sql_identifier_key(table.schema, table.schema_quoted)
            == sql_identifier_key(usage.schema, usage.schema_quoted)
        ]
    if usage.catalog:
        candidates = [
            table
            for table in candidates
            if sql_identifier_key(table.catalog, table.catalog_quoted)
            == sql_identifier_key(usage.catalog, usage.catalog_quoted)
        ]
    return candidates[0] if len(candidates) == 1 else None
