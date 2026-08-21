"""Same-database staging generations and atomic completed-graph publication."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.constraints import (
    LEGACY_ANALYZER_STABLE_LABELS,
    LEGACY_OWNER_MIGRATION_QUERY,
)
from graph.nodes.call_occurrence import CALL_OCCURRENCE_LABEL
from graph.nodes.import_occurrence import IMPORT_OCCURRENCE_LABEL
from graph.schema.rel_types import HAS_CALL_OCCURRENCE
from pipeline.models.call_resolution import (
    call_occurrence_node_id,
    call_resolution_from_storage,
    normalize_call_resolution_ledger,
    summarize_call_resolution_ledger,
)
from pipeline.models.import_resolution import (
    import_occurrence_node_id,
    import_resolution_from_storage,
    normalize_import_resolution_ledger,
    summarize_import_resolution_ledger,
)
from pipeline.models.semantic_frame import SemanticFrame


GENERATION_LABEL = "ANALYZER_GENERATION"
ACTIVE_GENERATION_LABEL = "ANALYZER_ACTIVE_GENERATION"
ACTIVE_GENERATION_KEY = "active"
COMPLETED_STATUSES = ("completed", "completed_with_findings")
TERMINAL_FAILURE_STATUSES = ("failed", "stopped")


class GraphGenerationError(RuntimeError):
    """The staged generation cannot be safely published or terminated."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_generation_client(client: Neo4jClient, generation_id: str) -> None:
    if not generation_id or client.generation_id != generation_id:
        raise GraphGenerationError(
            "Neo4jClient generation does not match the requested run generation"
        )


async def begin_generation(
    client: Neo4jClient,
    *,
    generation_id: str,
    strategy: str,
    source_ast_closure: dict[str, Any],
) -> None:
    """Create one immutable building manifest before any generation data write."""
    _require_generation_client(client, generation_id)
    expected_json = _canonical_json(source_ast_closure)
    await client.run_query(
        f"""
CREATE (g:{GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  run_id: $generation_id
}})
SET g.status = 'building',
    g.strategy = $strategy,
    g.started_at = $started_at,
    g.source_ast_expected_json = $source_ast_expected_json,
    g.source_ast_expected_sha256 = $source_ast_expected_sha256
""",
        {
            "graph_owner": ANALYZER_GRAPH_OWNER,
            "strategy": strategy,
            "started_at": _utc_now(),
            "source_ast_expected_json": expected_json,
            "source_ast_expected_sha256": hashlib.sha256(
                expected_json.encode("utf-8")
            ).hexdigest(),
        },
    )


async def bind_active_completed_generation(client: Neo4jClient) -> str:
    """Bind an operational async client to the active completed generation."""
    rows = await client.run_read_query(
        f"""
MATCH (active:{ACTIVE_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  key: $active_key
}})
MATCH (generation:{GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  run_id: active.current_generation_id
}})
WHERE generation.status IN $completed_statuses
RETURN generation.run_id AS generation_id
""",
        {
            "graph_owner": ANALYZER_GRAPH_OWNER,
            "active_key": ACTIVE_GENERATION_KEY,
            "completed_statuses": list(COMPLETED_STATUSES),
        },
    )
    if len(rows) != 1 or not rows[0].get("generation_id"):
        raise GraphGenerationError("no active completed Analyzer graph generation")
    generation_id = str(rows[0]["generation_id"])
    client.generation_id = generation_id
    return generation_id


def _node_identities(rows: list[dict]) -> list[dict]:
    identities: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        node_id = str(row.get("id") or "")
        if not node_id:
            raise GraphGenerationError("generation contains a node without canonical id")
        if node_id in seen:
            raise GraphGenerationError(
                f"generation contains duplicate canonical node id: {node_id}"
            )
        seen.add(node_id)
        identities.append({
            "id": node_id,
            "labels": sorted(
                label for label in (row.get("labels") or [])
                if label != "ANALYZER_OWNED"
            ),
        })
    return sorted(identities, key=lambda item: item["id"])


def _edge_identities(
    rows: list[dict], node_ids: set[str], generation_id: str,
) -> list[dict]:
    identities: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        source_id = str(row.get("source_id") or "")
        target_id = str(row.get("target_id") or "")
        rel_type = str(row.get("rel_type") or "")
        if not source_id or not target_id or not rel_type:
            raise GraphGenerationError("generation contains an edge with blank identity")
        if source_id not in node_ids or target_id not in node_ids:
            raise GraphGenerationError(
                f"generation edge endpoint is outside node closure: "
                f"{source_id}-[{rel_type}]->{target_id}"
            )
        properties = row.get("properties") or {}
        if properties.get("generation_id") != generation_id:
            raise GraphGenerationError(
                f"generation edge has missing or mismatched generation id: "
                f"{source_id}-[{rel_type}]->{target_id}"
            )
        identity = {
            "source_id": source_id,
            "type": rel_type,
            "target_id": target_id,
            "properties": properties,
        }
        canonical_identity = _canonical_json(identity)
        if canonical_identity in seen:
            raise GraphGenerationError(
                f"duplicate canonical edge identity: "
                f"{source_id}-[{rel_type}]->{target_id}"
            )
        seen.add(canonical_identity)
        identities.append(identity)
    return sorted(identities, key=_canonical_json)


def _slot_identities(rows: list[dict]) -> list[dict]:
    identities: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        raw = row.get("semantic_frame_json")
        if raw in (None, ""):
            continue
        node_id = str(row.get("id") or "")
        try:
            frame = SemanticFrame.model_validate_json(raw)
        except (TypeError, ValueError) as error:
            raise GraphGenerationError(
                f"invalid semantic_frame_json at {node_id}"
            ) from error
        for slot_id, slot in frame.slots.items():
            key = (node_id, slot_id)
            if key in seen:
                raise GraphGenerationError(
                    f"duplicate semantic slot identity: {node_id}::{slot_id}"
                )
            seen.add(key)
            identities.append({
                "node_id": node_id,
                "slot_id": slot_id,
                "type": f"{slot.family}.{slot.role}",
                "status": slot.status,
                "evidence_refs": sorted(slot.evidence_refs),
            })
    return sorted(identities, key=lambda item: (item["node_id"], item["slot_id"]))


def _call_occurrence_identities(
    rows: list[dict], node_ids: set[str], expected: list[dict],
) -> list[dict]:
    identities: list[dict] = []
    for row in rows:
        try:
            occurrence = call_resolution_from_storage(row.get("properties") or {})
        except ValueError as error:
            raise GraphGenerationError(f"invalid persisted call occurrence: {error}") from error
        evidence_node_id = call_occurrence_node_id(occurrence["call_id"])
        if evidence_node_id not in node_ids:
            raise GraphGenerationError(
                f"call occurrence evidence node is outside node closure: {evidence_node_id}"
            )
        owner_ids = sorted(str(value) for value in row.get("owner_ids") or [])
        required_owners = [occurrence["caller_id"]] if occurrence["caller_id"] else []
        if owner_ids != required_owners:
            raise GraphGenerationError(
                f"call occurrence owner edge mismatch: {occurrence['call_id']}"
            )
        if occurrence["caller_id"] and occurrence["caller_id"] not in node_ids:
            raise GraphGenerationError(
                f"call occurrence owner is outside node closure: {occurrence['caller_id']}"
            )
        identities.append(occurrence)
    try:
        normalized = normalize_call_resolution_ledger(identities)
    except ValueError as error:
        raise GraphGenerationError(f"invalid call occurrence ledger: {error}") from error
    if normalized != expected:
        raise GraphGenerationError("call occurrence ledger differs between workflow and Neo4j")
    return normalized


def _import_occurrence_identities(
    rows: list[dict], node_ids: set[str], expected: list[dict],
) -> list[dict]:
    identities: list[dict] = []
    for row in rows:
        try:
            occurrence = import_resolution_from_storage(
                row.get("properties") or {}
            )
        except ValueError as error:
            raise GraphGenerationError(
                f"invalid persisted import occurrence: {error}"
            ) from error
        evidence_node_id = import_occurrence_node_id(
            occurrence["import_id"], occurrence["entry_ordinal"],
        )
        if evidence_node_id not in node_ids:
            raise GraphGenerationError(
                "import occurrence evidence node is outside node closure: "
                f"{evidence_node_id}"
            )
        identities.append(occurrence)
    try:
        normalized = normalize_import_resolution_ledger(identities)
    except ValueError as error:
        raise GraphGenerationError(
            f"invalid import occurrence ledger: {error}"
        ) from error
    if normalized != expected:
        raise GraphGenerationError(
            "import occurrence ledger differs between workflow and Neo4j"
        )
    return normalized


async def publish_generation(
    client: Neo4jClient,
    *,
    generation_id: str,
    quality_status: Literal["completed", "completed_with_findings"],
    call_resolution_ledger: list[dict],
    import_resolution_ledger: list[dict] | None = None,
) -> dict[str, Any]:
    """Close actual graph identities and flip the active pointer in one transaction."""
    _require_generation_client(client, generation_id)
    if quality_status not in COMPLETED_STATUSES:
        raise GraphGenerationError(f"invalid publish status: {quality_status}")
    try:
        expected_call_occurrences = normalize_call_resolution_ledger(
            call_resolution_ledger
        )
    except ValueError as error:
        raise GraphGenerationError(f"invalid expected call occurrence ledger: {error}") from error
    try:
        expected_import_occurrences = normalize_import_resolution_ledger(
            import_resolution_ledger or []
        )
    except ValueError as error:
        raise GraphGenerationError(
            f"invalid expected import occurrence ledger: {error}"
        ) from error
    completed_at = _utc_now()

    async def _publish(transaction) -> dict[str, Any]:
        manifest_result = await transaction.run(
            f"""
MATCH (g:{GENERATION_LABEL} {{graph_owner: $graph_owner, run_id: $generation_id}})
WHERE g.status = 'building'
RETURN count(g) AS matched
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
        )
        manifest_record = await manifest_result.single(strict=True)
        if int(manifest_record["matched"]) != 1:
            raise GraphGenerationError("building generation manifest is missing or non-unique")

        node_result = await transaction.run(
            """
MATCH (n:ANALYZER_OWNED {graph_owner: $graph_owner, generation_id: $generation_id})
RETURN n.id AS id, labels(n) AS labels, n.semantic_frame_json AS semantic_frame_json
ORDER BY id
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
        )
        node_rows = await node_result.data()
        nodes = _node_identities(node_rows)
        if not nodes:
            raise GraphGenerationError("refusing to publish an empty graph generation")

        edge_result = await transaction.run(
            """
MATCH (a:ANALYZER_OWNED {graph_owner: $graph_owner, generation_id: $generation_id})
      -[r]->
      (b:ANALYZER_OWNED {graph_owner: $graph_owner, generation_id: $generation_id})
RETURN a.id AS source_id, type(r) AS rel_type, b.id AS target_id,
       properties(r) AS properties
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
        )
        edge_rows = await edge_result.data()
        node_ids = {item["id"] for item in nodes}
        edges = _edge_identities(edge_rows, node_ids, generation_id)
        slots = _slot_identities(node_rows)
        call_result = await transaction.run(
            f"""
MATCH (occurrence:ANALYZER_OWNED:{CALL_OCCURRENCE_LABEL} {{
  graph_owner: $graph_owner,
  generation_id: $generation_id
}})
OPTIONAL MATCH (owner:ANALYZER_OWNED {{
  graph_owner: $graph_owner,
  generation_id: $generation_id
}})-[:{HAS_CALL_OCCURRENCE}]->(occurrence)
RETURN properties(occurrence) AS properties,
       collect(owner.id) AS owner_ids
ORDER BY properties.ordinal
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
        )
        call_rows = await call_result.data()
        call_occurrences = _call_occurrence_identities(
            call_rows, node_ids, expected_call_occurrences,
        )
        call_summary = summarize_call_resolution_ledger(call_occurrences)
        import_result = await transaction.run(
            f"""
MATCH (occurrence:ANALYZER_OWNED:{IMPORT_OCCURRENCE_LABEL} {{
  graph_owner: $graph_owner,
  generation_id: $generation_id
}})
RETURN properties(occurrence) AS properties
ORDER BY properties.ordinal
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
        )
        import_rows = await import_result.data()
        import_occurrences = _import_occurrence_identities(
            import_rows, node_ids, expected_import_occurrences,
        )
        import_summary = summarize_import_resolution_ledger(import_occurrences)
        resolved_quality_status = (
            "completed_with_findings"
            if (
                quality_status == "completed_with_findings"
                or call_summary["unresolved"] or import_summary["unresolved"]
            )
            else "completed"
        )
        closure = {
            "schema_version": "graph-generation-closure/v3",
            "generation_id": generation_id,
            "quality_status": resolved_quality_status,
            "nodes": {"count": len(nodes), "sha256": _sha256(nodes), "identities": nodes},
            "edges": {"count": len(edges), "sha256": _sha256(edges), "identities": edges},
            "slots": {"count": len(slots), "sha256": _sha256(slots), "identities": slots},
            "call_occurrences": {
                "count": call_summary["total"],
                "unresolved": call_summary["unresolved"],
                "by_outcome": call_summary["by_outcome"],
                "by_reason": call_summary["by_reason"],
                "sha256": call_summary["sha256"],
                "identities": call_occurrences,
            },
            "import_occurrences": {
                "count": import_summary["total"],
                "unresolved": import_summary["unresolved"],
                "by_outcome": import_summary["by_outcome"],
                "by_reason": import_summary["by_reason"],
                "sha256": import_summary["sha256"],
                "identities": import_occurrences,
            },
        }
        manifest_json = _canonical_json({
            key: value if key not in {
                "nodes", "edges", "slots", "call_occurrences", "import_occurrences",
            } else {
                field: value[field] for field in value if field != "identities"
            }
            for key, value in closure.items()
        })
        call_resolution_summary_json = _canonical_json(call_summary)
        import_resolution_summary_json = _canonical_json(import_summary)

        publish_result = await transaction.run(
            f"""
MATCH (g:{GENERATION_LABEL} {{graph_owner: $graph_owner, run_id: $generation_id}})
WHERE g.status = 'building'
SET g.status = $quality_status,
    g.completed_at = $completed_at,
    g.graph_closure_json = $graph_closure_json,
    g.call_resolution_summary_json = $call_resolution_summary_json,
    g.call_resolution_ledger_sha256 = $call_resolution_ledger_sha256,
    g.call_resolution_population = $call_resolution_population,
    g.call_resolution_unresolved = $call_resolution_unresolved,
    g.import_resolution_summary_json = $import_resolution_summary_json,
    g.import_resolution_ledger_sha256 = $import_resolution_ledger_sha256,
    g.import_resolution_population = $import_resolution_population,
    g.import_resolution_unresolved = $import_resolution_unresolved
MERGE (active:{ACTIVE_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  key: $active_key
}})
SET active.current_generation_id = $generation_id,
    active.published_at = $completed_at
RETURN g.run_id AS run_id, active.current_generation_id AS active_generation_id
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
            quality_status=resolved_quality_status,
            completed_at=completed_at,
            graph_closure_json=manifest_json,
            call_resolution_summary_json=call_resolution_summary_json,
            call_resolution_ledger_sha256=call_summary["sha256"],
            call_resolution_population=call_summary["total"],
            call_resolution_unresolved=call_summary["unresolved"],
            import_resolution_summary_json=import_resolution_summary_json,
            import_resolution_ledger_sha256=import_summary["sha256"],
            import_resolution_population=import_summary["total"],
            import_resolution_unresolved=import_summary["unresolved"],
            active_key=ACTIVE_GENERATION_KEY,
        )
        record = await publish_result.single(strict=True)
        if record["run_id"] != generation_id or record["active_generation_id"] != generation_id:
            raise GraphGenerationError("active generation pointer publish did not close")
        migration_result = await transaction.run(
            LEGACY_OWNER_MIGRATION_QUERY,
            graph_owner=ANALYZER_GRAPH_OWNER,
            stable_labels=list(LEGACY_ANALYZER_STABLE_LABELS),
        )
        await migration_result.consume()
        cleanup_result = await transaction.run(
            """
MATCH (n:ANALYZER_OWNED {graph_owner: $graph_owner})
WHERE n.generation_id IS NULL OR n.generation_id <> $generation_id
DETACH DELETE n
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
        )
        await cleanup_result.consume()
        return closure

    return await client.execute_write(_publish)


async def terminate_generation(
    client: Neo4jClient,
    *,
    generation_id: str,
    status: Literal["failed", "stopped"],
    reason: str,
) -> None:
    """Terminally quarantine a non-published generation and delete only its data."""
    _require_generation_client(client, generation_id)
    if status not in TERMINAL_FAILURE_STATUSES:
        raise GraphGenerationError(f"invalid terminal generation status: {status}")

    async def _terminate(transaction) -> None:
        result = await transaction.run(
            f"""
MATCH (g:{GENERATION_LABEL} {{graph_owner: $graph_owner, run_id: $generation_id}})
WHERE g.status = 'building'
SET g.status = $status,
    g.terminal_at = $terminal_at,
    g.terminal_reason = $reason
RETURN count(g) AS matched
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
            status=status,
            terminal_at=_utc_now(),
            reason=reason,
        )
        record = await result.single(strict=True)
        if int(record["matched"]) != 1:
            raise GraphGenerationError("building generation manifest is missing or non-unique")
        delete_result = await transaction.run(
            """
MATCH (n:ANALYZER_OWNED {graph_owner: $graph_owner, generation_id: $generation_id})
DETACH DELETE n
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            generation_id=generation_id,
        )
        await delete_result.consume()

    await client.execute_write(_terminate)
