"""Validated product-graph publication and post-commit run sealing."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from graph.neo4j_client import Neo4jClient
from graph.product_writer import replace_product_graph
from pipeline.models.analysis_meta import AnalysisRun
from pipeline.models.call_resolution import normalize_call_resolution_ledger
from pipeline.models.import_resolution import normalize_import_resolution_ledger
from pipeline.publication.product_graph import (
    ProductGraphInput,
    build_product_graph,
)
from shared.events.ndjson_event_stream import NDJSONStream
from shared.events.runtime_logger import log_error, redact_sensitive_text


class PublishedRunUnsealed(RuntimeError):
    """Neo4j commit succeeded but one required completion artifact did not."""

    def __init__(self, stage: str, graph_closure: Mapping[str, Any]) -> None:
        super().__init__(
            f"published graph could not be sealed: stage={stage} "
            f"graph_sha256={graph_closure['graph_sha256']}"
        )
        self.stage = stage
        self.graph_closure = dict(graph_closure)


def build_product_graph_input(
    *,
    final_state: Mapping[str, Any],
    call_resolution_ledger: Sequence[Mapping[str, Any]],
    import_resolution_ledger: Sequence[Mapping[str, Any]],
) -> ProductGraphInput:
    """Bind the final workflow state to the closed product-plan contract."""
    final_call_ledger = normalize_call_resolution_ledger(
        final_state["call_resolution_ledger"]
    )
    captured_call_ledger = normalize_call_resolution_ledger(call_resolution_ledger)
    if final_call_ledger != captured_call_ledger:
        raise ValueError(
            "final workflow call ledger differs from its sole streamed producer"
        )
    final_import_ledger = normalize_import_resolution_ledger(
        final_state["import_resolution_ledger"]
    )
    captured_import_ledger = normalize_import_resolution_ledger(
        import_resolution_ledger
    )
    if final_import_ledger != captured_import_ledger:
        raise ValueError(
            "final workflow import ledger differs from its sole streamed producer"
        )

    return ProductGraphInput(
        packages=final_state["packages"],
        modules=final_state["modules"],
        code_nodes=final_state["code_nodes"],
        variables=final_state["variables"],
        constants=final_state["constants"],
        data_types=final_state["data_types"],
        parent_of_edges=final_state["parent_of_edges"],
        statement_next_edges=final_state["statement_next_edges"],
        references_edges=final_state["references_edges"],
        init_by_edges=final_state["init_by_edges"],
        tables=final_state["tables"],
        columns=final_state["columns"],
        has_column_edges=final_state["has_column_edges"],
        foreign_key_edges=final_state["fk_edges"],
        # Optional external-format branches can leave a None channel placeholder
        # in the final checkpoint when no verified chunk was produced.  It is not
        # a source record and must not be projected as an EXTERNAL_CHUNK node.
        external_source_records=tuple(
            record
            for record in final_state["external_source_records"]
            if record is not None
        ),
        resolved_external_reference_links=(
            final_state["resolved_external_reference_links"]
        ),
        calls_edges=final_state["calls_edges"],
        reads_writes_edges=final_state["reads_writes_edges"],
        rules=final_state["rules"],
        has_rule_edges=final_state["has_rule_edges"],
        rule_table_edges=final_state["rule_table_edges"],
        routine_embeddings=final_state["routine_embeddings"],
    )


def write_performance_artifact(run_dir: Path, payload: Mapping[str, Any]) -> str:
    """Persist one canonical performance ledger and return its byte hash."""
    encoded = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    artifact_dir = run_dir / "performance"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_dir / f"{digest}.json"
    if target.exists():
        if target.read_bytes() != encoded:
            raise ValueError("content-addressed performance artifact hash collision")
    else:
        temporary = artifact_dir / f"{digest}.json.tmp"
        temporary.write_bytes(encoded)
        temporary.replace(target)
    return digest


def write_product_graph_receipt(
    run_dir: Path,
    receipt: Mapping[str, Any],
) -> None:
    """Persist the complete verified product ledger after publication."""
    target = run_dir / "product-graph-receipt.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(dict(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def update_run_manifest(
    run_dir: Path,
    *,
    status: str,
    graph_closure: Mapping[str, Any] | None = None,
) -> None:
    """Atomically write the run status and optional published graph receipt."""
    target = run_dir / "manifest.json"
    manifest = json.loads(target.read_text(encoding="utf-8"))
    manifest["status"] = status
    if graph_closure is not None:
        manifest["product_graph"] = {
            "sha256": graph_closure["graph_sha256"],
            "node_count": graph_closure["node_count"],
            "relationship_count": graph_closure["relationship_count"],
        }
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def seal_published_run(
    *,
    run: AnalysisRun,
    graph_closure: dict[str, Any],
    prepublish_performance_artifact_hash: str,
    publish_transaction_wall_time_ms: int,
    run_started_at: float,
    quality_status: str,
    stream: NDJSONStream | None,
) -> None:
    """Persist every post-commit artifact before a run may report completion."""
    stage = "product_graph_receipt"
    try:
        write_product_graph_receipt(run.run_dir, graph_closure)
        stage = "postcommit_performance"
        write_performance_artifact(
            run.run_dir,
            {
                "schema_version": "postcommit-performance/v1",
                "prepublish_performance_artifact_hash": (
                    prepublish_performance_artifact_hash
                ),
                "product_graph_sha256": graph_closure["graph_sha256"],
                "product_node_count": graph_closure["node_count"],
                "product_relationship_count": graph_closure["relationship_count"],
                "publish_transaction_wall_time_ms": publish_transaction_wall_time_ms,
                "total_wall_time_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        stage = "terminal_manifest"
        update_run_manifest(
            run.run_dir,
            status=quality_status,
            graph_closure=graph_closure,
        )
    except Exception as error:
        recovery_error: Exception | None = None
        try:
            update_run_manifest(
                run.run_dir,
                status="published_unsealed",
                graph_closure=graph_closure,
            )
        except Exception as manifest_error:
            recovery_error = manifest_error
        if stream is not None:
            stream.emit({
                "type": "phase_event",
                "event": "published_unsealed",
                "run_id": run.run_id,
                "failed_stage": stage,
                "graph_sha256": graph_closure["graph_sha256"],
            })
        log_error(
            "run",
            "published_run_seal_failed",
            error,
            stream_emit=stream.emit if stream is not None else None,
            run_id=run.run_id,
            failed_stage=stage,
            graph_sha256=graph_closure["graph_sha256"],
            manifest_recovery_error=(
                redact_sensitive_text(str(recovery_error))
                if recovery_error is not None else None
            ),
        )
        raise PublishedRunUnsealed(stage, graph_closure) from error


async def publish_completed_run(
    *,
    run: AnalysisRun,
    final_state: Mapping[str, Any],
    call_resolution_ledger: Sequence[Mapping[str, Any]],
    import_resolution_ledger: Sequence[Mapping[str, Any]],
    workflow_node_completion_timeline: Sequence[Mapping[str, Any]],
    run_started_at: float,
    quality_status: str,
    stream: NDJSONStream | None,
) -> dict[str, Any]:
    """Build, atomically publish, and seal one validated product graph."""
    prepublish_wall_time_ms = int((time.monotonic() - run_started_at) * 1000)
    prepublish_hash = write_performance_artifact(
        run.run_dir,
        {
            "schema_version": "prepublish-performance/v1",
            "measurement_boundary": (
                "run_start_through_final_workflow_state_before_"
                "product_graph_build_and_publish"
            ),
            "prepublish_wall_time_ms": prepublish_wall_time_ms,
            "workflow_node_completion_timeline": list(
                workflow_node_completion_timeline
            ),
        },
    )
    product_graph = build_product_graph(build_product_graph_input(
        final_state=final_state,
        call_resolution_ledger=call_resolution_ledger,
        import_resolution_ledger=import_resolution_ledger,
    ))
    client = Neo4jClient()
    try:
        publish_started_at = time.monotonic()
        receipt = await replace_product_graph(client, product_graph)
        publish_wall_time_ms = int(
            (time.monotonic() - publish_started_at) * 1000
        )
    finally:
        await client.close()

    graph_closure = {
        "graph_sha256": receipt.graph_sha256,
        "node_count": receipt.node_count,
        "relationship_count": receipt.relationship_count,
    }
    seal_published_run(
        run=run,
        graph_closure=graph_closure,
        prepublish_performance_artifact_hash=prepublish_hash,
        publish_transaction_wall_time_ms=publish_wall_time_ms,
        run_started_at=run_started_at,
        quality_status=quality_status,
        stream=stream,
    )
    return graph_closure
