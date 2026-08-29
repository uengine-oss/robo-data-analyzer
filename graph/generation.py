"""Same-database staging generations and atomic completed-graph publication."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Literal

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.product_batch_writer import verify_product_plan_before_publication
from graph.product_write_batch import (
    NodeIdentity,
    ProductWriteContractError,
    ProductWritePlan,
    canonical_operation_key,
)
from pipeline.models.call_resolution import (
    normalize_call_resolution_ledger,
    summarize_call_resolution_ledger,
)
from pipeline.models.import_resolution import (
    normalize_import_resolution_ledger,
    summarize_import_resolution_ledger,
)
from graph.publication_schema import (
    ANALYSIS_GENERATION_LABEL,
    ANALYSIS_SCOPE_LABEL,
    PRODUCT_STAGE_NAMES,
)


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


_SCOPE_CONTROL_PROPERTY_KEYS = (
    "analysis_scope_id",
    "publication_version",
    "active_generation_id",
)
_GENERATION_CONTROL_PROPERTY_KEYS = (
    "analysis_scope_id",
    "generation_id",
    "generation_status",
    "publication_status",
    "expected_publication_version",
    "expected_active_generation_id",
)


def _canonical_control_record_hash(
    *,
    record_type: str,
    identity: str,
    property_keys: tuple[str, ...],
    present_properties: dict[str, Any],
) -> str:
    """Hash one control record with explicit property presence and primitive type."""
    unexpected = sorted(set(present_properties) - set(property_keys))
    if unexpected:
        raise GraphGenerationError(
            f"unexpected control record properties for {identity}: {unexpected}"
        )
    properties: list[dict[str, Any]] = []
    for key in property_keys:
        if key not in present_properties:
            properties.append({"key": key, "presence": "absent"})
            continue
        properties.append({
            "key": key,
            "presence": "present",
            "typed_value": _typed_property_value(
                present_properties[key], identity=identity, key=key,
            ),
        })
    return _sha256({
        "schema_version": "analysis-control-record-hash/v1",
        "record_type": record_type,
        "identity": identity,
        "properties": properties,
    })


def _scope_control_hash(
    analysis_scope_id: str,
    *,
    publication_version: int,
    active_generation_id: str | None,
) -> tuple[str, str]:
    identity = f"AnalysisScope:{analysis_scope_id}"
    properties: dict[str, Any] = {
        "analysis_scope_id": analysis_scope_id,
        "publication_version": publication_version,
    }
    if active_generation_id:
        properties["active_generation_id"] = active_generation_id
    return identity, _canonical_control_record_hash(
        record_type=ANALYSIS_SCOPE_LABEL,
        identity=identity,
        property_keys=_SCOPE_CONTROL_PROPERTY_KEYS,
        present_properties=properties,
    )


def _generation_control_hash(
    analysis_scope_id: str,
    generation_id: str,
    *,
    generation_status: str,
    publication_status: str,
    expected_publication_version: int | None = None,
    expected_active_generation_id: str | None = None,
) -> tuple[str, str]:
    identity = f"AnalysisGeneration:{analysis_scope_id}:{generation_id}"
    properties: dict[str, Any] = {
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
        "generation_status": generation_status,
        "publication_status": publication_status,
    }
    if expected_publication_version is not None:
        properties["expected_publication_version"] = expected_publication_version
    if expected_active_generation_id is not None:
        properties["expected_active_generation_id"] = expected_active_generation_id
    return identity, _canonical_control_record_hash(
        record_type=ANALYSIS_GENERATION_LABEL,
        identity=identity,
        property_keys=_GENERATION_CONTROL_PROPERTY_KEYS,
        present_properties=properties,
    )


def _control_hash_ledger(*records: tuple[str, str]) -> dict[str, str]:
    ledger = dict(records)
    if len(ledger) != len(records):
        raise GraphGenerationError("duplicate control record identity in hash ledger")
    return dict(sorted(ledger.items()))


def _parse_control_hash_ledger(value: object, *, field_name: str) -> dict[str, str]:
    if not isinstance(value, str) or not value:
        raise GraphGenerationError(f"{field_name} is missing")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise GraphGenerationError(f"{field_name} is not canonical JSON") from error
    if not isinstance(parsed, dict) or any(
        not isinstance(identity, str)
        or not identity
        or not isinstance(record_hash, str)
        or len(record_hash) != 64
        for identity, record_hash in parsed.items()
    ):
        raise GraphGenerationError(f"{field_name} is malformed")
    if _canonical_json(parsed) != value:
        raise GraphGenerationError(f"{field_name} is not canonically serialized")
    return dict(parsed)


def _require_generation_client(client: Neo4jClient, generation_id: str) -> str:
    if not generation_id or client.generation_id != generation_id:
        raise GraphGenerationError(
            "Neo4jClient generation does not match the requested run generation"
        )
    analysis_scope_id = getattr(client, "analysis_scope_id", None)
    if not isinstance(analysis_scope_id, str) or not analysis_scope_id:
        raise GraphGenerationError(
            "Neo4jClient has no stable analysis_scope_id"
        )
    return analysis_scope_id


async def begin_generation(
    client: Neo4jClient,
    *,
    generation_id: str,
    strategy: str,
    source_ast_closure: dict[str, Any],
) -> None:
    """Create one building manifest and seal/read back its control pre-state."""
    analysis_scope_id = _require_generation_client(client, generation_id)
    expected_json = _canonical_json(source_ast_closure)
    expected_sha256 = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()

    async def _begin(transaction) -> None:
        prestate_result = await transaction.run(
            f"""
MERGE (scope:{ANALYSIS_SCOPE_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id
}})
ON CREATE SET scope.publication_version = 0
SET scope.publication_version = coalesce(scope.publication_version, 0)
WITH scope
OPTIONAL MATCH (previous:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: scope.active_generation_id
}})
RETURN scope.publication_version AS publication_version,
       scope.active_generation_id AS active_generation_id,
       previous.generation_status AS previous_generation_status,
       previous.publication_status AS previous_publication_status,
       previous.expected_publication_version AS previous_expected_publication_version,
       previous.expected_active_generation_id AS previous_expected_active_generation_id
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
        )
        prestate = await prestate_result.single(strict=True)
        if prestate is None or not isinstance(
            prestate.get("publication_version"), int,
        ):
            raise GraphGenerationError("analysis scope control pre-state is invalid")
        publication_version = int(prestate["publication_version"])
        active_generation_id = prestate.get("active_generation_id")
        if active_generation_id is not None and (
            not isinstance(active_generation_id, str) or not active_generation_id
        ):
            raise GraphGenerationError("analysis scope active generation ID is invalid")
        previous_generation_status = prestate.get("previous_generation_status")
        previous_publication_status = prestate.get("previous_publication_status")
        previous_expected_publication_version = prestate.get(
            "previous_expected_publication_version"
        )
        previous_expected_active_generation_id = prestate.get(
            "previous_expected_active_generation_id"
        )
        if active_generation_id:
            if (
                previous_generation_status not in COMPLETED_STATUSES
                or previous_publication_status != "active"
                or not isinstance(previous_expected_publication_version, int)
                or not isinstance(previous_expected_active_generation_id, str)
            ):
                raise GraphGenerationError(
                    "analysis scope active pointer does not identify one active "
                    "completed generation"
                )
        elif (
            previous_generation_status is not None
            or previous_publication_status is not None
            or previous_expected_publication_version is not None
            or previous_expected_active_generation_id is not None
        ):
            raise GraphGenerationError(
                "analysis scope without an active pointer returned a previous generation"
            )

        initial_records = [_scope_control_hash(
            analysis_scope_id,
            publication_version=publication_version,
            active_generation_id=active_generation_id,
        )]
        locked_records = [_scope_control_hash(
            analysis_scope_id,
            publication_version=publication_version + 1,
            active_generation_id=active_generation_id,
        )]
        if active_generation_id:
            previous_record = _generation_control_hash(
                analysis_scope_id,
                active_generation_id,
                generation_status=str(previous_generation_status),
                publication_status=str(previous_publication_status),
                expected_publication_version=previous_expected_publication_version,
                expected_active_generation_id=previous_expected_active_generation_id,
            )
            initial_records.append(previous_record)
            locked_records.append(previous_record)
        building_record = _generation_control_hash(
            analysis_scope_id,
            generation_id,
            generation_status="building",
            publication_status="building",
            expected_publication_version=publication_version,
            expected_active_generation_id=active_generation_id or "",
        )
        locked_records.append(building_record)
        initial_control_hashes_json = _canonical_json(
            _control_hash_ledger(*initial_records)
        )
        locked_control_hashes_json = _canonical_json(
            _control_hash_ledger(*locked_records)
        )

        result = await transaction.run(
            f"""
CREATE (g:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
SET g.generation_status = 'building',
    g.publication_status = 'building',
    g.strategy = $strategy,
    g.started_at = $started_at,
    g.source_ast_expected_json = $source_ast_expected_json,
    g.source_ast_expected_sha256 = $source_ast_expected_sha256,
    g.expected_publication_version = $expected_publication_version,
    g.expected_active_generation_id = $expected_active_generation_id,
    g.initial_control_record_hashes_json = $initial_control_record_hashes_json,
    g.expected_locked_control_pretransition_hashes_json = $expected_locked_control_pretransition_hashes_json,
    g.building_control_record_sha256 = $building_control_record_sha256
RETURN g.generation_id AS generation_id,
       g.generation_status AS generation_status,
       g.publication_status AS publication_status,
       g.expected_publication_version AS expected_publication_version,
       g.expected_active_generation_id AS expected_active_generation_id,
       g.initial_control_record_hashes_json AS initial_control_record_hashes_json,
       g.expected_locked_control_pretransition_hashes_json AS expected_locked_control_pretransition_hashes_json,
       g.building_control_record_sha256 AS building_control_record_sha256
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
            strategy=strategy,
            started_at=_utc_now(),
            source_ast_expected_json=expected_json,
            source_ast_expected_sha256=expected_sha256,
            expected_publication_version=publication_version,
            expected_active_generation_id=active_generation_id or "",
            initial_control_record_hashes_json=initial_control_hashes_json,
            expected_locked_control_pretransition_hashes_json=locked_control_hashes_json,
            building_control_record_sha256=building_record[1],
        )
        record = await result.single(strict=True)
        if (
            record is None
            or record.get("generation_id") != generation_id
            or record.get("generation_status") != "building"
            or record.get("publication_status") != "building"
            or record.get("expected_publication_version") != publication_version
            or record.get("expected_active_generation_id")
            != (active_generation_id or "")
            or record.get("initial_control_record_hashes_json")
            != initial_control_hashes_json
            or record.get("expected_locked_control_pretransition_hashes_json")
            != locked_control_hashes_json
            or record.get("building_control_record_sha256") != building_record[1]
        ):
            raise GraphGenerationError(
                "building generation control record read-back did not close"
            )

    await client.execute_write(_begin)


async def bind_active_completed_generation(client: Neo4jClient) -> str:
    """Bind an operational async client to the active completed generation."""
    analysis_scope_id = getattr(client, "analysis_scope_id", None)
    if not isinstance(analysis_scope_id, str) or not analysis_scope_id:
        raise GraphGenerationError("active generation lookup requires analysis_scope_id")
    rows = await client.run_read_query(
        f"""
MATCH (active:{ANALYSIS_SCOPE_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id
}})
MATCH (generation:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: active.active_generation_id
}})
WHERE generation.generation_status IN $completed_statuses
RETURN generation.generation_id AS generation_id
""",
        {
            "graph_owner": ANALYZER_GRAPH_OWNER,
            "analysis_scope_id": analysis_scope_id,
            "completed_statuses": list(COMPLETED_STATUSES),
        },
    )
    if len(rows) != 1 or not rows[0].get("generation_id"):
        raise GraphGenerationError("no active completed Analyzer graph generation")
    generation_id = str(rows[0]["generation_id"])
    client.generation_id = generation_id
    return generation_id


def _typed_property_value(value: Any, *, identity: str, key: str) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GraphGenerationError(
                f"generation property is not finite: {identity}.{key}"
            )
        return {"type": "float", "value": value}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, list):
        if not value:
            return {"type": "list:empty", "value": []}
        typed_items = [
            _typed_property_value(item, identity=identity, key=f"{key}[]")
            for item in value
        ]
        item_types = {item["type"] for item in typed_items}
        if len(item_types) != 1 or any(
            item_type.startswith("list:") for item_type in item_types
        ):
            raise GraphGenerationError(
                f"generation property list is nested or heterogeneous: {identity}.{key}"
            )
        item_type = next(iter(item_types))
        return {
            "type": f"list:{item_type}",
            "value": [item["value"] for item in typed_items],
        }
    raise GraphGenerationError(
        f"generation property type is unsupported: {identity}.{key}"
    )


async def publish_generation(
    client: Neo4jClient,
    *,
    generation_id: str,
    quality_status: Literal["completed", "completed_with_findings"],
    call_resolution_ledger: list[dict],
    import_resolution_ledger: list[dict] | None = None,
    product_plan: ProductWritePlan,
) -> dict[str, Any]:
    """Verify the complete product plan and atomically publish its generation."""
    analysis_scope_id = _require_generation_client(client, generation_id)
    if (
        product_plan.analysis_scope_id != analysis_scope_id
        or product_plan.generation_id != generation_id
    ):
        raise GraphGenerationError(
            "product plan identity does not match the publication generation"
        )
    product_stage_names = {batch.stage_name for batch in product_plan.batches}
    if product_stage_names != PRODUCT_STAGE_NAMES:
        raise GraphGenerationError(
            "publication requires the complete closed product stage set"
        )
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
        lock_result = await transaction.run(
            f"""
MATCH (g:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
MATCH (scope:{ANALYSIS_SCOPE_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id
}})
WHERE g.generation_status = 'building'
  AND g.publication_status = 'building'
  AND scope.publication_version = g.expected_publication_version
  AND coalesce(scope.active_generation_id, '') = g.expected_active_generation_id
OPTIONAL MATCH (previous:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: scope.active_generation_id
}})
SET scope.publication_version = scope.publication_version + 1
RETURN g.generation_id AS generation_id,
       g.generation_status AS generation_status,
       g.publication_status AS publication_status,
       g.expected_publication_version AS expected_publication_version,
       g.expected_active_generation_id AS previous_active_generation_id,
       g.initial_control_record_hashes_json AS initial_control_record_hashes_json,
       g.expected_locked_control_pretransition_hashes_json AS expected_locked_control_pretransition_hashes_json,
       scope.active_generation_id AS locked_active_generation_id,
       scope.publication_version AS locked_publication_version,
       previous.generation_status AS previous_generation_status,
       previous.publication_status AS previous_publication_status,
       previous.expected_publication_version AS previous_expected_publication_version,
       previous.expected_active_generation_id AS previous_expected_active_generation_id
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        lock_rows = await lock_result.data()
        if len(lock_rows) != 1:
            raise GraphGenerationError(
                "publication CAS conflict or building generation is unavailable"
            )
        lock_record = lock_rows[0]
        previous_active_generation_id = str(
            lock_record.get("previous_active_generation_id") or ""
        )
        expected_publication_version = lock_record.get("expected_publication_version")
        locked_publication_version = lock_record.get("locked_publication_version")
        if (
            lock_record.get("generation_id") != generation_id
            or lock_record.get("generation_status") != "building"
            or lock_record.get("publication_status") != "building"
            or not isinstance(expected_publication_version, int)
            or locked_publication_version != expected_publication_version + 1
            or lock_record.get("locked_active_generation_id")
            != (previous_active_generation_id or None)
        ):
            raise GraphGenerationError("publication CAS lock state is invalid")

        previous_generation_status = lock_record.get("previous_generation_status")
        previous_publication_status = lock_record.get("previous_publication_status")
        previous_expected_publication_version = lock_record.get(
            "previous_expected_publication_version"
        )
        previous_expected_active_generation_id = lock_record.get(
            "previous_expected_active_generation_id"
        )
        locked_records = [_scope_control_hash(
            analysis_scope_id,
            publication_version=locked_publication_version,
            active_generation_id=previous_active_generation_id or None,
        )]
        if previous_active_generation_id:
            if (
                previous_generation_status not in COMPLETED_STATUSES
                or previous_publication_status != "active"
                or not isinstance(previous_expected_publication_version, int)
                or not isinstance(previous_expected_active_generation_id, str)
            ):
                raise GraphGenerationError(
                    "locked previous generation control state is invalid"
                )
            locked_records.append(_generation_control_hash(
                analysis_scope_id,
                previous_active_generation_id,
                generation_status=str(previous_generation_status),
                publication_status=str(previous_publication_status),
                expected_publication_version=previous_expected_publication_version,
                expected_active_generation_id=previous_expected_active_generation_id,
            ))
        elif (
            previous_generation_status is not None
            or previous_publication_status is not None
            or previous_expected_publication_version is not None
            or previous_expected_active_generation_id is not None
        ):
            raise GraphGenerationError(
                "locked scope without an active pointer returned a previous generation"
            )
        locked_records.append(_generation_control_hash(
            analysis_scope_id,
            generation_id,
            generation_status="building",
            publication_status="building",
            expected_publication_version=expected_publication_version,
            expected_active_generation_id=previous_active_generation_id,
        ))
        actual_locked_control_hashes = _control_hash_ledger(*locked_records)
        expected_locked_control_hashes = _parse_control_hash_ledger(
            lock_record.get("expected_locked_control_pretransition_hashes_json"),
            field_name="expected_locked_control_pretransition_hashes_json",
        )
        if actual_locked_control_hashes != expected_locked_control_hashes:
            raise GraphGenerationError(
                "locked control pre-transition read-back hash mismatch"
            )
        verified_locked_control_hashes_json = _canonical_json(
            actual_locked_control_hashes
        )
        initial_control_hashes = _parse_control_hash_ledger(
            lock_record.get("initial_control_record_hashes_json"),
            field_name="initial_control_record_hashes_json",
        )
        initial_control_hashes_json = _canonical_json(initial_control_hashes)

        try:
            product_receipt = await verify_product_plan_before_publication(
                transaction,
                product_plan,
            )
        except ProductWriteContractError as error:
            raise GraphGenerationError(
                f"product plan verification failed: {error}"
            ) from error
        if not product_receipt.record_hash_by_identity:
            raise GraphGenerationError(
                "refusing to publish an empty product generation"
            )

        call_occurrences = expected_call_occurrences
        call_summary = summarize_call_resolution_ledger(call_occurrences)
        import_occurrences = expected_import_occurrences
        import_summary = summarize_import_resolution_ledger(import_occurrences)
        health_expectations = [
            expectation
            for expectation in product_plan.expectations
            if (
                isinstance(expectation.identity, NodeIdentity)
                and expectation.identity.product_type == "AnalysisHealth"
            )
        ]
        if len(health_expectations) != 1:
            raise GraphGenerationError(
                "product plan must contain exactly one AnalysisHealth record"
            )
        health_properties = health_expectations[0].property_map
        if (
            health_properties.get("missing_total") != 0
            or health_properties.get("duplicate_total") != 0
            or health_properties.get("unresolved_call_count")
            != call_summary["unresolved"]
        ):
            raise GraphGenerationError(
                "AnalysisHealth does not match the final completeness ledgers"
            )
        unresolved_data_object_count = health_properties.get(
            "unresolved_data_object_count"
        )
        if (
            isinstance(unresolved_data_object_count, bool)
            or not isinstance(unresolved_data_object_count, int)
            or unresolved_data_object_count < 0
        ):
            raise GraphGenerationError(
                "AnalysisHealth unresolved data-object count is invalid"
            )
        resolved_quality_status = (
            "completed_with_findings"
            if (
                quality_status == "completed_with_findings"
                or call_summary["unresolved"]
                or import_summary["unresolved"]
                or unresolved_data_object_count
            )
            else "completed"
        )
        expected_post_records = [
            _scope_control_hash(
                analysis_scope_id,
                publication_version=locked_publication_version,
                active_generation_id=generation_id,
            ),
            _generation_control_hash(
                analysis_scope_id,
                generation_id,
                generation_status=resolved_quality_status,
                publication_status="active",
                expected_publication_version=expected_publication_version,
                expected_active_generation_id=previous_active_generation_id,
            ),
        ]
        if previous_active_generation_id:
            expected_post_records.append(_generation_control_hash(
                analysis_scope_id,
                previous_active_generation_id,
                generation_status=str(previous_generation_status),
                publication_status="superseded",
                expected_publication_version=previous_expected_publication_version,
                expected_active_generation_id=previous_expected_active_generation_id,
            ))
        expected_post_control_hashes = _control_hash_ledger(
            *expected_post_records
        )
        expected_post_control_hashes_json = _canonical_json(
            expected_post_control_hashes
        )
        product_stage_identities = [
            {"stage_name": name, "sha256": digest}
            for name, digest in product_receipt.stage_hash_by_name
        ]
        product_operation_identities = sorted(
            canonical_operation_key(key)
            for key in product_plan.required_operation_key_set
        )
        product_record_identities = [
            {"identity": identity, "sha256": digest}
            for identity, digest in product_receipt.record_hash_by_identity
        ]
        expected_stage_hashes = tuple(sorted(
            (batch.stage_name, batch.canonical_batch_sha256)
            for batch in product_plan.batches
        ))
        if (
            product_receipt.stage_hash_by_name != expected_stage_hashes
            or product_receipt.operation_count
            != len(product_plan.required_operation_key_set)
            or product_receipt.record_hash_by_identity
            != product_plan.required_product_record_hash_by_identity
        ):
            raise GraphGenerationError(
                "verified product receipt differs from the immutable plan"
            )
        product_plan_sha256 = _sha256({
            "schema_version": "product-write-plan/v1",
            "analysis_scope_id": analysis_scope_id,
            "generation_id": generation_id,
            "stages": product_stage_identities,
            "operations": product_operation_identities,
            "property_owners": list(
                product_plan.property_owner_by_identity_and_key
            ),
            "records": product_record_identities,
        })
        closure = {
            "schema_version": "product-generation-closure/v1",
            "analysis_scope_id": analysis_scope_id,
            "generation_id": generation_id,
            "quality_status": resolved_quality_status,
            "product_plan_sha256": product_plan_sha256,
            "control_records": {
                "initial_sha256": _sha256(initial_control_hashes),
                "locked_pretransition_sha256": _sha256(
                    actual_locked_control_hashes
                ),
                "post_state_sha256": _sha256(expected_post_control_hashes),
            },
            "product_stages": {
                "count": len(product_stage_identities),
                "sha256": _sha256(product_stage_identities),
                "identities": product_stage_identities,
            },
            "product_operations": {
                "count": len(product_operation_identities),
                "sha256": _sha256(product_operation_identities),
                "identities": product_operation_identities,
            },
            "product_records": {
                "count": len(product_record_identities),
                "sha256": _sha256(product_record_identities),
                "identities": product_record_identities,
            },
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
                "product_stages", "product_operations", "product_records",
                "call_occurrences", "import_occurrences",
            } else {
                field: value[field] for field in value if field != "identities"
            }
            for key, value in closure.items()
        })
        call_resolution_summary_json = _canonical_json(call_summary)
        import_resolution_summary_json = _canonical_json(import_summary)

        publish_result = await transaction.run(
            f"""
MATCH (g:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
MATCH (scope:{ANALYSIS_SCOPE_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id
}})
WHERE g.generation_status = 'building'
  AND g.publication_status = 'building'
  AND scope.publication_version = $locked_publication_version
  AND coalesce(scope.active_generation_id, '') = $previous_active_generation_id
OPTIONAL MATCH (previous:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $previous_active_generation_id
}})
FOREACH (_ IN CASE WHEN previous IS NULL THEN [] ELSE [1] END |
  SET previous.publication_status = 'superseded',
      previous.superseded_at = $completed_at
)
SET g.generation_status = $quality_status,
    g.publication_status = 'active',
    g.completed_at = $completed_at,
    g.graph_closure_json = $graph_closure_json,
    g.call_resolution_summary_json = $call_resolution_summary_json,
    g.call_resolution_ledger_sha256 = $call_resolution_ledger_sha256,
    g.call_resolution_population = $call_resolution_population,
    g.call_resolution_unresolved = $call_resolution_unresolved,
    g.import_resolution_summary_json = $import_resolution_summary_json,
    g.import_resolution_ledger_sha256 = $import_resolution_ledger_sha256,
    g.import_resolution_population = $import_resolution_population,
    g.import_resolution_unresolved = $import_resolution_unresolved,
    g.product_plan_sha256 = $product_plan_sha256,
    g.product_stage_count = $product_stage_count,
    g.product_operation_count = $product_operation_count,
    g.product_record_count = $product_record_count,
    g.verified_locked_control_pretransition_hashes_json = $verified_locked_control_pretransition_hashes_json,
    g.expected_post_control_record_hashes_json = $expected_post_control_record_hashes_json,
    scope.active_generation_id = $generation_id,
    scope.published_at = $completed_at
RETURN g.generation_id AS generation_id,
       scope.active_generation_id AS active_generation_id,
       scope.publication_version AS publication_version
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
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
            product_plan_sha256=product_plan_sha256,
            product_stage_count=len(product_stage_identities),
            product_operation_count=len(product_operation_identities),
            product_record_count=len(product_record_identities),
            verified_locked_control_pretransition_hashes_json=(
                verified_locked_control_hashes_json
            ),
            expected_post_control_record_hashes_json=(
                expected_post_control_hashes_json
            ),
            previous_active_generation_id=previous_active_generation_id,
            locked_publication_version=locked_publication_version,
        )
        record = await publish_result.single(strict=True)
        if (
            record is None
            or record.get("generation_id") != generation_id
            or record.get("active_generation_id") != generation_id
            or record.get("publication_version") != locked_publication_version
        ):
            raise GraphGenerationError("active generation pointer publish did not close")

        post_state_result = await transaction.run(
            f"""
MATCH (scope:{ANALYSIS_SCOPE_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id
}})
MATCH (current:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
OPTIONAL MATCH (previous:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $previous_active_generation_id
}})
RETURN scope.active_generation_id AS active_generation_id,
       scope.publication_version AS publication_version,
       current.generation_status AS current_quality_status,
       current.publication_status AS current_publication_status,
       current.expected_publication_version AS current_expected_publication_version,
       current.expected_active_generation_id AS current_expected_active_generation_id,
       current.product_plan_sha256 AS current_product_plan_sha256,
       current.product_stage_count AS current_product_stage_count,
       current.product_operation_count AS current_product_operation_count,
       current.product_record_count AS current_product_record_count,
       current.verified_locked_control_pretransition_hashes_json AS verified_locked_control_pretransition_hashes_json,
       current.expected_post_control_record_hashes_json AS expected_post_control_record_hashes_json,
       previous.generation_status AS previous_generation_status,
       previous.publication_status AS previous_publication_status,
       previous.expected_publication_version AS previous_expected_publication_version,
       previous.expected_active_generation_id AS previous_expected_active_generation_id
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
            previous_active_generation_id=previous_active_generation_id,
        )
        post_state = await post_state_result.single(strict=True)
        expected_previous_status = (
            "superseded" if previous_active_generation_id else None
        )
        if (
            post_state is None
            or post_state.get("active_generation_id") != generation_id
            or post_state.get("publication_version") != locked_publication_version
            or post_state.get("current_quality_status") != resolved_quality_status
            or post_state.get("current_publication_status") != "active"
            or post_state.get("current_expected_publication_version")
            != expected_publication_version
            or post_state.get("current_expected_active_generation_id")
            != previous_active_generation_id
            or post_state.get("current_product_plan_sha256")
            != product_plan_sha256
            or post_state.get("current_product_stage_count")
            != len(product_stage_identities)
            or post_state.get("current_product_operation_count")
            != len(product_operation_identities)
            or post_state.get("current_product_record_count")
            != len(product_record_identities)
            or post_state.get(
                "verified_locked_control_pretransition_hashes_json"
            ) != verified_locked_control_hashes_json
            or post_state.get("expected_post_control_record_hashes_json")
            != expected_post_control_hashes_json
            or post_state.get("previous_publication_status")
            != expected_previous_status
        ):
            raise GraphGenerationError("publication post-state verification failed")

        actual_post_records = [
            _scope_control_hash(
                analysis_scope_id,
                publication_version=int(post_state["publication_version"]),
                active_generation_id=str(post_state["active_generation_id"]),
            ),
            _generation_control_hash(
                analysis_scope_id,
                generation_id,
                generation_status=str(post_state["current_quality_status"]),
                publication_status=str(post_state["current_publication_status"]),
                expected_publication_version=post_state.get(
                    "current_expected_publication_version"
                ),
                expected_active_generation_id=post_state.get(
                    "current_expected_active_generation_id"
                ),
            ),
        ]
        if previous_active_generation_id:
            if (
                post_state.get("previous_generation_status")
                != previous_generation_status
                or post_state.get("previous_expected_publication_version")
                != previous_expected_publication_version
                or post_state.get("previous_expected_active_generation_id")
                != previous_expected_active_generation_id
            ):
                raise GraphGenerationError(
                    "previous generation post-state verification failed"
                )
            actual_post_records.append(_generation_control_hash(
                analysis_scope_id,
                previous_active_generation_id,
                generation_status=str(post_state["previous_generation_status"]),
                publication_status=str(post_state["previous_publication_status"]),
                expected_publication_version=post_state.get(
                    "previous_expected_publication_version"
                ),
                expected_active_generation_id=post_state.get(
                    "previous_expected_active_generation_id"
                ),
            ))
        actual_post_control_hashes = _control_hash_ledger(*actual_post_records)
        if actual_post_control_hashes != expected_post_control_hashes:
            raise GraphGenerationError("publication post-state read-back hash mismatch")
        verified_post_control_hashes_json = _canonical_json(
            actual_post_control_hashes
        )

        active_count_result = await transaction.run(
            f"""
MATCH (active:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id
}})
WHERE active.publication_status = 'active'
WITH collect(active) AS active_generations
MATCH (current:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
SET current.verified_post_control_record_hashes_json = $verified_post_control_record_hashes_json
RETURN size(active_generations) AS active_count,
       [active IN active_generations | active.generation_id] AS active_generation_ids,
       current.verified_post_control_record_hashes_json AS verified_post_control_record_hashes_json
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
            verified_post_control_record_hashes_json=(
                verified_post_control_hashes_json
            ),
        )
        active_count_state = await active_count_result.single(strict=True)
        if (
            active_count_state is None
            or active_count_state.get("active_count") != 1
            or active_count_state.get("active_generation_ids") != [generation_id]
            or active_count_state.get("verified_post_control_record_hashes_json")
            != verified_post_control_hashes_json
        ):
            raise GraphGenerationError(
                "publication must leave exactly one active generation"
            )
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
    analysis_scope_id = _require_generation_client(client, generation_id)
    if status not in TERMINAL_FAILURE_STATUSES:
        raise GraphGenerationError(f"invalid terminal generation status: {status}")

    async def _terminate(transaction) -> None:
        result = await transaction.run(
            f"""
MATCH (g:{ANALYSIS_GENERATION_LABEL} {{
  graph_owner: $graph_owner,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
WHERE g.generation_status = 'building'
SET g.generation_status = $status,
    g.terminal_at = $terminal_at,
    g.terminal_reason = $reason
RETURN count(g) AS matched
""",
            graph_owner=ANALYZER_GRAPH_OWNER,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
            status=status,
            terminal_at=_utc_now(),
            reason=reason,
        )
        record = await result.single(strict=True)
        if int(record["matched"]) != 1:
            raise GraphGenerationError("building generation manifest is missing or non-unique")
        delete_result = await transaction.run(
            f"""
MATCH (n {{
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
WHERE NOT n:{ANALYSIS_GENERATION_LABEL}
DETACH DELETE n
""",
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        await delete_result.consume()

    await client.execute_write(_terminate)
