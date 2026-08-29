"""Transactional Neo4j writer for immutable :class:`ProductWriteBatch` values."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from graph.neo4j_client import Neo4jClient
from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    NodePropertySetOperation,
    ProductIdentity,
    ProductWriteBatch,
    ProductWritePlan,
    ProductWriteContractError,
    PropertyValue,
    RelationshipCreateOperation,
    RelationshipIdentity,
    RelationshipPropertySetOperation,
    canonical_operation_key,
    verify_product_write_batch_readback,
    verify_product_write_plan_readback,
)
from graph.publication_schema import (
    ANALYSIS_GENERATION_LABEL,
    PRODUCT_NODE_TYPES,
    PRODUCT_RELATIONSHIP_TYPES,
)


@dataclass(frozen=True)
class ProductWriteReceipt:
    """Verified result of one new write or one exact idempotent replay."""

    stage_name: str
    canonical_batch_sha256: str
    operation_count: int
    record_hash_by_identity: tuple[tuple[str, str], ...]
    idempotent_replay: bool


@dataclass(frozen=True)
class ProductPlanVerificationReceipt:
    """Exact final product and write-ledger read-back before publication."""

    stage_hash_by_name: tuple[tuple[str, str], ...]
    operation_count: int
    record_hash_by_identity: tuple[tuple[str, str], ...]


def _neo4j_value(value: PropertyValue) -> object:
    return list(value) if isinstance(value, tuple) else value


def _neo4j_properties(
    properties: tuple[tuple[str, PropertyValue], ...],
) -> dict[str, object]:
    return {key: _neo4j_value(value) for key, value in properties}


def _require_client_identity(
    client: Neo4jClient,
    batch: ProductWriteBatch,
) -> None:
    if client.analysis_scope_id != batch.analysis_scope_id:
        raise ProductWriteContractError(
            "Neo4jClient analysis_scope_id differs from ProductWriteBatch"
        )
    if client.generation_id != batch.generation_id:
        raise ProductWriteContractError(
            "Neo4jClient generation_id differs from ProductWriteBatch"
        )


def _validated_existing_ledger(record: object) -> tuple[dict[str, str], set[str], int]:
    if record is None:
        raise ProductWriteContractError(
            "building AnalysisGeneration is missing for product write"
        )
    stage_names = list(record.get("stage_names") or [])
    stage_hashes = list(record.get("stage_hashes") or [])
    operation_keys = list(record.get("operation_keys") or [])
    ledger_version = record.get("ledger_version")
    if (
        len(stage_names) != len(stage_hashes)
        or len(stage_names) != len(set(stage_names))
        or len(operation_keys) != len(set(operation_keys))
        or not isinstance(ledger_version, int)
    ):
        raise ProductWriteContractError(
            "AnalysisGeneration product write ledger is malformed"
        )
    if any(not isinstance(value, str) or not value for value in (
        *stage_names, *stage_hashes, *operation_keys,
    )):
        raise ProductWriteContractError(
            "AnalysisGeneration product write ledger contains a blank/non-string value"
        )
    return dict(zip(stage_names, stage_hashes, strict=True)), set(operation_keys), ledger_version


async def _create_nodes(transaction, operations: Iterable[NodeCreateOperation]) -> None:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for operation in operations:
        grouped[operation.identity.product_type].append({
            "stable_id": operation.identity.stable_id,
            "properties": _neo4j_properties(operation.present_properties),
        })
    for product_type, items in sorted(grouped.items()):
        result = await transaction.run(
            f"""
/* product-node-create */
UNWIND $items AS item
CREATE (node:{product_type})
SET node += item.properties
RETURN node.stable_id AS stable_id, properties(node) AS properties
""",
            items=items,
        )
        rows = await result.data()
        if len(rows) != len(items):
            raise ProductWriteContractError(
                f"node create returned {len(rows)} of {len(items)} records: "
                f"{product_type}"
            )


async def _patch_nodes(
    transaction,
    operations: Iterable[NodePropertySetOperation],
    *,
    analysis_scope_id: str,
    generation_id: str,
) -> None:
    grouped: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for operation in operations:
        by_identity = grouped[operation.identity.product_type]
        properties = by_identity.setdefault(operation.identity.stable_id, {})
        properties[operation.property_key] = _neo4j_value(operation.value)
    for product_type, by_identity in sorted(grouped.items()):
        items = [
            {"stable_id": stable_id, "properties": properties}
            for stable_id, properties in sorted(by_identity.items())
        ]
        result = await transaction.run(
            f"""
/* product-node-patch */
UNWIND $items AS item
MATCH (node:{product_type} {{
  stable_id: item.stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
SET node += item.properties
RETURN node.stable_id AS stable_id, properties(node) AS properties
""",
            items=items,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        rows = await result.data()
        if len(rows) != len(items):
            raise ProductWriteContractError(
                f"node patch matched {len(rows)} of {len(items)} records: "
                f"{product_type}"
            )


async def _create_relationships(
    transaction,
    operations: Iterable[RelationshipCreateOperation],
    *,
    analysis_scope_id: str,
    generation_id: str,
) -> None:
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for operation in operations:
        identity = operation.identity
        grouped[(
            identity.relationship_type,
            identity.source.product_type,
            identity.target.product_type,
        )].append({
            "source_stable_id": identity.source.stable_id,
            "target_stable_id": identity.target.stable_id,
            "properties": _neo4j_properties(operation.present_properties),
        })
    for (relationship_type, source_type, target_type), items in sorted(grouped.items()):
        result = await transaction.run(
            f"""
/* product-relationship-create */
UNWIND $items AS item
MATCH (source:{source_type} {{
  stable_id: item.source_stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
MATCH (target:{target_type} {{
  stable_id: item.target_stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
CREATE (source)-[relationship:{relationship_type}]->(target)
SET relationship += item.properties
RETURN source.stable_id AS source_stable_id,
       target.stable_id AS target_stable_id,
       properties(relationship) AS properties
""",
            items=items,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        rows = await result.data()
        if len(rows) != len(items):
            raise ProductWriteContractError(
                f"relationship create matched {len(rows)} of {len(items)} records: "
                f"{relationship_type}"
            )


async def _patch_relationships(
    transaction,
    operations: Iterable[RelationshipPropertySetOperation],
    *,
    analysis_scope_id: str,
    generation_id: str,
) -> None:
    grouped: dict[tuple[str, str, str], dict[tuple[str, str], dict[str, object]]] = (
        defaultdict(dict)
    )
    for operation in operations:
        identity = operation.identity
        by_endpoints = grouped[(
            identity.relationship_type,
            identity.source.product_type,
            identity.target.product_type,
        )]
        properties = by_endpoints.setdefault((
            identity.source.stable_id,
            identity.target.stable_id,
        ), {})
        properties[operation.property_key] = _neo4j_value(operation.value)
    for (relationship_type, source_type, target_type), by_endpoints in sorted(
        grouped.items(),
    ):
        items = [
            {
                "source_stable_id": source_stable_id,
                "target_stable_id": target_stable_id,
                "properties": properties,
            }
            for (source_stable_id, target_stable_id), properties
            in sorted(by_endpoints.items())
        ]
        result = await transaction.run(
            f"""
/* product-relationship-patch */
UNWIND $items AS item
MATCH (source:{source_type} {{
  stable_id: item.source_stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})-[relationship:{relationship_type} {{
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}}]->(target:{target_type} {{
  stable_id: item.target_stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
SET relationship += item.properties
RETURN source.stable_id AS source_stable_id,
       target.stable_id AS target_stable_id,
       properties(relationship) AS properties
""",
            items=items,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        rows = await result.data()
        if len(rows) != len(items):
            raise ProductWriteContractError(
                f"relationship patch matched {len(rows)} of {len(items)} records: "
                f"{relationship_type}"
            )


async def _read_nodes(
    transaction,
    identities: Iterable[NodeIdentity],
    *,
    analysis_scope_id: str,
    generation_id: str,
) -> list[tuple[ProductIdentity, dict]]:
    grouped: dict[str, list[NodeIdentity]] = defaultdict(list)
    for identity in identities:
        grouped[identity.product_type].append(identity)
    actual: list[tuple[ProductIdentity, dict]] = []
    for product_type, typed_identities in sorted(grouped.items()):
        identity_by_stable_id = {
            identity.stable_id: identity for identity in typed_identities
        }
        result = await transaction.run(
            f"""
/* product-node-readback */
UNWIND $stable_ids AS stable_id
MATCH (node:{product_type} {{
  stable_id: stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
RETURN node.stable_id AS stable_id, properties(node) AS properties
""",
            stable_ids=sorted(identity_by_stable_id),
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        for row in await result.data():
            stable_id = str(row.get("stable_id") or "")
            identity = identity_by_stable_id.get(stable_id)
            if identity is None:
                raise ProductWriteContractError(
                    f"node read-back returned an unexpected stable_id: {stable_id!r}"
                )
            actual.append((identity, dict(row.get("properties") or {})))
    return actual


async def _read_relationships(
    transaction,
    identities: Iterable[RelationshipIdentity],
    *,
    analysis_scope_id: str,
    generation_id: str,
) -> list[tuple[ProductIdentity, dict]]:
    grouped: dict[tuple[str, str, str], list[RelationshipIdentity]] = defaultdict(list)
    for identity in identities:
        grouped[(
            identity.relationship_type,
            identity.source.product_type,
            identity.target.product_type,
        )].append(identity)
    actual: list[tuple[ProductIdentity, dict]] = []
    for (relationship_type, source_type, target_type), typed_identities in sorted(
        grouped.items()
    ):
        identity_by_endpoints = {
            (identity.source.stable_id, identity.target.stable_id): identity
            for identity in typed_identities
        }
        items = [
            {"source_stable_id": source, "target_stable_id": target}
            for source, target in sorted(identity_by_endpoints)
        ]
        result = await transaction.run(
            f"""
/* product-relationship-readback */
UNWIND $items AS item
MATCH (source:{source_type} {{
  stable_id: item.source_stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})-[relationship:{relationship_type}]->(target:{target_type} {{
  stable_id: item.target_stable_id,
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
RETURN source.stable_id AS source_stable_id,
       target.stable_id AS target_stable_id,
       properties(relationship) AS properties
""",
            items=items,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
        )
        for row in await result.data():
            endpoints = (
                str(row.get("source_stable_id") or ""),
                str(row.get("target_stable_id") or ""),
            )
            identity = identity_by_endpoints.get(endpoints)
            if identity is None:
                raise ProductWriteContractError(
                    f"relationship read-back returned unexpected endpoints: {endpoints}"
                )
            actual.append((identity, dict(row.get("properties") or {})))
    return actual


async def write_product_batch(
    client: Neo4jClient,
    batch: ProductWriteBatch,
) -> ProductWriteReceipt:
    """Write and verify one complete product stage in one managed transaction."""
    _require_client_identity(client, batch)
    serialized_operation_keys = {
        canonical_operation_key(key) for key in batch.required_operation_key_set
    }

    async def _write(transaction) -> ProductWriteReceipt:
        ledger_result = await transaction.run(
            f"""
/* product-write-ledger-lock */
MATCH (generation:{ANALYSIS_GENERATION_LABEL} {{
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
WHERE generation.generation_status = 'building'
SET generation.write_ledger_version =
    coalesce(generation.write_ledger_version, 0) + 1
RETURN coalesce(generation.completed_product_stage_names, []) AS stage_names,
       coalesce(generation.completed_product_stage_hashes, []) AS stage_hashes,
       coalesce(generation.written_operation_keys, []) AS operation_keys,
       generation.write_ledger_version AS ledger_version
""",
            analysis_scope_id=batch.analysis_scope_id,
            generation_id=batch.generation_id,
        )
        stage_hash_by_name, written_operation_keys, ledger_version = (
            _validated_existing_ledger(await ledger_result.single(strict=True))
        )
        existing_hash = stage_hash_by_name.get(batch.stage_name)
        idempotent_replay = existing_hash is not None
        if idempotent_replay:
            if existing_hash != batch.canonical_batch_sha256:
                raise ProductWriteContractError(
                    f"product stage replay changed its immutable batch: "
                    f"{batch.stage_name}"
                )
            if not serialized_operation_keys.issubset(written_operation_keys):
                raise ProductWriteContractError(
                    f"completed product stage is missing operation keys: "
                    f"{batch.stage_name}"
                )
        else:
            overlap = serialized_operation_keys & written_operation_keys
            if overlap:
                raise ProductWriteContractError(
                    f"product operation key already belongs to another stage: "
                    f"{sorted(overlap)}"
                )
            await _create_nodes(transaction, batch.node_create_operations)
            await _patch_nodes(
                transaction,
                batch.node_property_set_operations,
                analysis_scope_id=batch.analysis_scope_id,
                generation_id=batch.generation_id,
            )
            await _create_relationships(
                transaction,
                batch.relationship_create_operations,
                analysis_scope_id=batch.analysis_scope_id,
                generation_id=batch.generation_id,
            )
            await _patch_relationships(
                transaction,
                batch.relationship_property_set_operations,
                analysis_scope_id=batch.analysis_scope_id,
                generation_id=batch.generation_id,
            )

        node_identities = [
            expectation.identity
            for expectation in batch.expectations
            if isinstance(expectation.identity, NodeIdentity)
        ]
        relationship_identities = [
            expectation.identity
            for expectation in batch.expectations
            if isinstance(expectation.identity, RelationshipIdentity)
        ]
        actual_records = await _read_nodes(
            transaction,
            node_identities,
            analysis_scope_id=batch.analysis_scope_id,
            generation_id=batch.generation_id,
        )
        actual_records.extend(await _read_relationships(
            transaction,
            relationship_identities,
            analysis_scope_id=batch.analysis_scope_id,
            generation_id=batch.generation_id,
        ))
        record_hashes = verify_product_write_batch_readback(batch, actual_records)

        if not idempotent_replay:
            stage_hash_by_name[batch.stage_name] = batch.canonical_batch_sha256
            written_operation_keys.update(serialized_operation_keys)
            ordered_stages = sorted(stage_hash_by_name)
            update_result = await transaction.run(
                f"""
/* product-write-ledger-commit */
MATCH (generation:{ANALYSIS_GENERATION_LABEL} {{
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
WHERE generation.generation_status = 'building'
  AND generation.write_ledger_version = $ledger_version
SET generation.completed_product_stage_names = $stage_names,
    generation.completed_product_stage_hashes = $stage_hashes,
    generation.written_operation_keys = $operation_keys
RETURN generation.generation_id AS generation_id
""",
                analysis_scope_id=batch.analysis_scope_id,
                generation_id=batch.generation_id,
                ledger_version=ledger_version,
                stage_names=ordered_stages,
                stage_hashes=[stage_hash_by_name[name] for name in ordered_stages],
                operation_keys=sorted(written_operation_keys),
            )
            record = await update_result.single(strict=True)
            if record is None or record.get("generation_id") != batch.generation_id:
                raise ProductWriteContractError(
                    f"product write ledger commit failed: {batch.stage_name}"
                )

        return ProductWriteReceipt(
            stage_name=batch.stage_name,
            canonical_batch_sha256=batch.canonical_batch_sha256,
            operation_count=len(batch.required_operation_key_set),
            record_hash_by_identity=record_hashes,
            idempotent_replay=idempotent_replay,
        )

    return await client.execute_write(_write)


def _node_identity_from_labels(labels: object, stable_id: object) -> NodeIdentity:
    if not isinstance(labels, list) or any(
        not isinstance(label, str) or not label for label in labels
    ):
        raise ProductWriteContractError("product node read-back labels are malformed")
    product_labels = set(labels) & PRODUCT_NODE_TYPES
    if len(product_labels) != 1 or set(labels) != product_labels:
        raise ProductWriteContractError(
            f"product node must have exactly one closed product label: {labels!r}"
        )
    if not isinstance(stable_id, str) or not stable_id:
        raise ProductWriteContractError("product node stable_id is missing")
    return NodeIdentity(next(iter(product_labels)), stable_id)


async def verify_product_plan_before_publication(
    transaction,
    plan: ProductWritePlan,
) -> ProductPlanVerificationReceipt:
    """Verify the complete DB product and exact-once stage ledger in one transaction."""
    ledger_result = await transaction.run(
        f"""
/* product-plan-ledger-readback */
MATCH (generation:{ANALYSIS_GENERATION_LABEL} {{
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
WHERE generation.generation_status = 'building'
RETURN coalesce(generation.completed_product_stage_names, []) AS stage_names,
       coalesce(generation.completed_product_stage_hashes, []) AS stage_hashes,
       coalesce(generation.written_operation_keys, []) AS operation_keys,
       coalesce(generation.write_ledger_version, 0) AS ledger_version
""",
        analysis_scope_id=plan.analysis_scope_id,
        generation_id=plan.generation_id,
    )
    stage_hash_by_name, operation_keys, _ledger_version = (
        _validated_existing_ledger(await ledger_result.single(strict=True))
    )
    expected_stage_hashes = {
        batch.stage_name: batch.canonical_batch_sha256 for batch in plan.batches
    }
    if stage_hash_by_name != expected_stage_hashes:
        missing = sorted(set(expected_stage_hashes) - set(stage_hash_by_name))
        unexpected = sorted(set(stage_hash_by_name) - set(expected_stage_hashes))
        changed = sorted(
            stage_name
            for stage_name in set(stage_hash_by_name) & set(expected_stage_hashes)
            if stage_hash_by_name[stage_name] != expected_stage_hashes[stage_name]
        )
        raise ProductWriteContractError(
            "product stage ledger mismatch: "
            f"missing={missing}, unexpected={unexpected}, changed={changed}"
        )
    expected_operation_keys = {
        canonical_operation_key(key) for key in plan.required_operation_key_set
    }
    if operation_keys != expected_operation_keys:
        missing = sorted(expected_operation_keys - operation_keys)
        unexpected = sorted(operation_keys - expected_operation_keys)
        raise ProductWriteContractError(
            "product operation ledger mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )

    node_result = await transaction.run(
        f"""
/* product-plan-node-readback */
MATCH (node {{
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
}})
WHERE NOT node:{ANALYSIS_GENERATION_LABEL}
RETURN labels(node) AS labels,
       node.stable_id AS stable_id,
       properties(node) AS properties
""",
        analysis_scope_id=plan.analysis_scope_id,
        generation_id=plan.generation_id,
    )
    actual_records: list[tuple[ProductIdentity, dict]] = []
    actual_nodes: set[NodeIdentity] = set()
    for row in await node_result.data():
        identity = _node_identity_from_labels(
            row.get("labels"), row.get("stable_id"),
        )
        if identity in actual_nodes:
            raise ProductWriteContractError(
                f"duplicate final product node identity: {identity.canonical_key()}"
            )
        actual_nodes.add(identity)
        actual_records.append((identity, dict(row.get("properties") or {})))

    cross_edge_result = await transaction.run(
        """
/* product-plan-cross-scope-edge-readback */
MATCH (inside {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})-[relationship]-(outside)
WHERE outside.analysis_scope_id <> $analysis_scope_id
   OR outside.generation_id <> $generation_id
   OR outside.analysis_scope_id IS NULL
   OR outside.generation_id IS NULL
RETURN count(DISTINCT relationship) AS cross_scope_edge_count
""",
        analysis_scope_id=plan.analysis_scope_id,
        generation_id=plan.generation_id,
    )
    cross_edge_record = await cross_edge_result.single(strict=True)
    if (
        cross_edge_record is None
        or not isinstance(cross_edge_record.get("cross_scope_edge_count"), int)
        or cross_edge_record["cross_scope_edge_count"] != 0
    ):
        raise ProductWriteContractError(
            "product graph has a relationship outside its analysis scope/generation"
        )

    relationship_result = await transaction.run(
        """
/* product-plan-relationship-readback */
MATCH (source {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})-[relationship]->(target {
  analysis_scope_id: $analysis_scope_id,
  generation_id: $generation_id
})
RETURN labels(source) AS source_labels,
       source.stable_id AS source_stable_id,
       type(relationship) AS relationship_type,
       labels(target) AS target_labels,
       target.stable_id AS target_stable_id,
       properties(relationship) AS properties
""",
        analysis_scope_id=plan.analysis_scope_id,
        generation_id=plan.generation_id,
    )
    for row in await relationship_result.data():
        relationship_type = row.get("relationship_type")
        if relationship_type not in PRODUCT_RELATIONSHIP_TYPES:
            raise ProductWriteContractError(
                f"unknown final product relationship type: {relationship_type!r}"
            )
        source = _node_identity_from_labels(
            row.get("source_labels"), row.get("source_stable_id"),
        )
        target = _node_identity_from_labels(
            row.get("target_labels"), row.get("target_stable_id"),
        )
        if source not in actual_nodes or target not in actual_nodes:
            raise ProductWriteContractError(
                "product relationship endpoint is outside final node read-back"
            )
        actual_records.append((
            RelationshipIdentity(str(relationship_type), source, target),
            dict(row.get("properties") or {}),
        ))

    record_hashes = verify_product_write_plan_readback(plan, actual_records)
    return ProductPlanVerificationReceipt(
        stage_hash_by_name=tuple(sorted(stage_hash_by_name.items())),
        operation_count=len(operation_keys),
        record_hash_by_identity=record_hashes,
    )
