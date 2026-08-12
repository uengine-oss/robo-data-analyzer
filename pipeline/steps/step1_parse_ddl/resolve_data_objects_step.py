"""Reference-scoped Catalog resolution and confirmed-only sample materialization (spec 126)."""
from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from typing import Optional

import httpx
from langchain_core.runnables import RunnableConfig

from graph.edges.has_column import upsert_has_column
from graph.neo4j_client import Neo4jClient
from graph.nodes.column import upsert_columns
from graph.nodes.table import upsert_tables, update_sample_snapshots
from graph.schema.graph_edges import HasColumn
from graph.schema.graph_nodes import CodeNode, Column, Table
from integrations.catalog import catalog_base_url_or_skip, post_object_resolution_context
from pipeline.execution.graph_delta_stream import emit_edges, emit_message, emit_nodes
from pipeline.execution.step_config import step_config
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_info, log_warning

_STEP = "step1.resolve_data_objects"
_SAMPLE_LIMIT = 5


async def resolve_data_objects_step(
    state: dict, config: Optional[RunnableConfig] = None,
) -> dict:
    """Resolve parser evidence in one Catalog batch before DBMS R/W linking."""
    cfg = step_config(config)
    codebase = cfg.get("codebase")
    neo: Neo4jClient | None = cfg.get("neo_client")
    stream = cfg.get("stream")
    datasource = (getattr(codebase, "datasource", None) or "").strip()
    code_nodes: list[CodeNode] = list(state.get("code_nodes") or ())
    if not datasource or not code_nodes:
        log_info(_STEP, "skip", reason="datasource 또는 code_nodes 없음")
        return {}

    keyed: list[tuple[str, CodeNode, int, DataObjectUsageEvidence]] = []
    requests: list[dict] = []
    for node in code_nodes:
        for index, usage in enumerate(node.extra.get("data_object_usages") or ()):
            key = _usage_key(node.id, index, usage)
            keyed.append((key, node, index, usage))
            requests.append({
                "key": key,
                "raw_reference": usage.raw_reference,
                "schema_name": usage.schema,
                "object_name": usage.name,
                "database_link": usage.database_link,
                "case_sensitive_identifiers": bool(
                    usage.schema_quoted or usage.name_quoted
                    or any(column.name_quoted for column in usage.qualified_columns)
                ),
                "qualified_columns": sorted({
                    column.name for column in usage.qualified_columns
                }),
            })
    if not requests:
        log_info(_STEP, "skip", reason="versioned data-object reference 없음")
        return {}

    base_url = catalog_base_url_or_skip(_STEP)
    if base_url is None:
        return {}
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=SETTINGS.catalog_api.timeout_seconds) as http:
        payload = await post_object_resolution_context(
            http, base_url, datasource, requests, sample_limit=_SAMPLE_LIMIT,
        )
    if payload is None:
        log_warning(
            _STEP, "datasource_not_found",
            "catalog/fabric datasource 미등록 — DDL exact resolver와 unresolved 보존으로 진행",
            datasource=datasource,
        )
        return {}

    results = {str(item.get("key") or ""): item for item in payload.get("results") or ()}
    missing = [key for key, *_ in keyed if key not in results]
    if missing:
        raise ValueError(f"catalog object resolution missing keys: {missing[:5]}")

    tables: list[Table] = list(state.get("tables") or ())
    columns: list[Column] = list(state.get("columns") or ())
    has_columns: list[HasColumn] = list(state.get("has_column_edges") or ())
    table_by_identity = {
        (table.db.upper(), table.schema.upper(), table.name.upper()): table
        for table in tables
    }
    column_ids = {column.id for column in columns}
    edge_ids = {(edge.parent_id, edge.child_id) for edge in has_columns}
    new_tables: list[Table] = []
    new_columns: list[Column] = []
    new_edges: list[HasColumn] = []
    samples: dict[str, dict] = {}
    updated_by_node: dict[str, list[DataObjectUsageEvidence]] = {
        node.id: list(node.extra.get("data_object_usages") or ()) for node in code_nodes
    }
    unresolved = 0

    for key, node, index, usage in keyed:
        scoped_usage = replace(usage, datasource=datasource)
        updated_by_node[node.id][index] = scoped_usage
        result = results[key]
        if result.get("status") != "confirmed":
            unresolved += 1
            continue
        schema = str(result.get("schema_name") or usage.schema)
        name = str(result.get("object_name") or usage.name)
        identity = (datasource.upper(), schema.upper(), name.upper())
        table = table_by_identity.get(identity)
        if table is None:
            table = Table(
                db=datasource, schema=schema, name=name,
                description_source="catalog",
            )
            table_by_identity[identity] = table
            tables.append(table)
            new_tables.append(table)
        updated_by_node[node.id][index] = scoped_usage.resolved(table.id)
        response_columns = list(result.get("columns") or ())
        for raw_column in response_columns:
            column_name = str(raw_column.get("name") or "").strip()
            if not column_name:
                continue
            column_id = f"{table.id.removeprefix('db:')}.{column_name}"
            if column_id not in column_ids:
                column = Column(
                    id=column_id,
                    name=column_name,
                    dtype=str(raw_column.get("dtype") or raw_column.get("type") or ""),
                    nullable=bool(raw_column.get("nullable", True)),
                    description_source="catalog",
                )
                column_ids.add(column_id)
                columns.append(column)
                new_columns.append(column)
            if (table.id, column_id) not in edge_ids:
                edge = HasColumn(parent_id=table.id, child_id=column_id)
                edge_ids.add((table.id, column_id))
                has_columns.append(edge)
                new_edges.append(edge)
        rows = list(result.get("sample_rows") or ())
        if response_columns or rows:
            samples[table.id] = {"columns": response_columns, "sample_rows": rows}

    for node in code_nodes:
        if node.id in updated_by_node:
            node.extra["data_object_usages"] = tuple(updated_by_node[node.id])
            node.extra["unresolved_data_object_usages"] = tuple(
                usage for usage in updated_by_node[node.id]
                if usage.resolution != "exact"
            )

    if neo is not None:
        if new_tables:
            await upsert_tables(neo, new_tables)
        if new_columns:
            await upsert_columns(neo, new_columns)
        if new_edges:
            await upsert_has_column(neo, new_edges)
        await update_sample_snapshots(neo, samples)
    await emit_nodes(stream, [*new_tables, *new_columns])
    await emit_edges(stream, new_edges)
    emit_message(
        stream,
        f"✅ 객체 신원 해소 — confirmed {len(keyed) - unresolved} · unresolved {unresolved}",
    )
    metrics = dict(payload.get("metrics") or {})
    log_info(
        _STEP, "done",
        references=len(keyed), confirmed=len(keyed) - unresolved, unresolved=unresolved,
        provider_calls=metrics.get("provider_calls"),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "tables": tables,
        "columns": columns,
        "has_column_edges": has_columns,
        "table_sample_data": samples,
        "data_object_resolution_metrics": metrics,
    }


def _usage_key(
    source_id: str, index: int, usage: DataObjectUsageEvidence,
) -> str:
    raw = "\0".join((
        source_id, str(index), usage.raw_reference, usage.access,
        str(usage.evidence_line),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
