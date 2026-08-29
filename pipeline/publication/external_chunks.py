"""Publish exact-key external source records and verified REFER_TO links."""
from __future__ import annotations

from typing import Iterable, Mapping

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.schema.graph_nodes import CodeNode
from graph.schema.node_ids import published_external_chunk_id
from pipeline.languages import ROUTINE_LABELS
from pipeline.models.external_reference_resolution import (
    ExternalReferenceResolutionLedger,
    LinkedExternalReferenceResolution,
    ResolvedExternalReferenceLink,
)
from pipeline.models.external_source import ExternalSourceRecord


class ExternalChunksProjectionError(ValueError):
    pass


EXTERNAL_CHUNK_PROPERTY_KEYS = frozenset({
    "analysis_scope_id",
    "generation_id",
    "stable_id",
    "external_key",
    "semantic_kind",
    "source_file_id",
    "source_record_identity",
    "external_adapter_id",
    "external_adapter_version",
    "start_line",
    "end_line",
    "content_sha256",
    "exact_text",
})
REFER_TO_PROPERTY_KEYS = frozenset({"analysis_scope_id", "generation_id"})


def build_external_chunks_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    code_nodes: Iterable[CodeNode],
    source_records: Iterable[ExternalSourceRecord],
    resolved_links: Iterable[ResolvedExternalReferenceLink],
    external_reference_ledger: ExternalReferenceResolutionLedger | Mapping,
) -> ProductWriteBatch:
    """Build the complete product projection of linked external references."""
    routine_ids = {
        node.id for node in code_nodes if node.label in ROUTINE_LABELS
    }
    if any(not routine_id for routine_id in routine_ids):
        raise ExternalChunksProjectionError("routine IDs must be non-empty")
    records: dict[str, ExternalSourceRecord] = {}
    for record in source_records:
        if record.source_record_id in records:
            raise ExternalChunksProjectionError(
                f"duplicate external source record: {record.source_record_id!r}"
            )
        records[record.source_record_id] = record
    try:
        ledger = (
            external_reference_ledger
            if isinstance(external_reference_ledger, ExternalReferenceResolutionLedger)
            else ExternalReferenceResolutionLedger.model_validate(
                external_reference_ledger
            )
        )
    except ValueError as error:
        raise ExternalChunksProjectionError(str(error)) from error

    linked: set[tuple[str, str, str]] = set()
    for occurrence in ledger.occurrences:
        resolution = occurrence.resolution
        if not isinstance(resolution, LinkedExternalReferenceResolution):
            continue
        owner_id = occurrence.owner_code_node_id
        source_record_id = resolution.linked.external_source_record_id
        external_key = resolution.linked.external_key
        if owner_id not in routine_ids:
            raise ExternalChunksProjectionError(
                f"external reference owner is not a routine: {owner_id!r}"
            )
        record = records.get(source_record_id)
        if record is None:
            raise ExternalChunksProjectionError(
                f"linked external source record is missing: {source_record_id!r}"
            )
        if external_key not in record.addressable_keys:
            raise ExternalChunksProjectionError(
                "linked external key is not addressable in its source record: "
                f"{external_key!r}"
            )
        linked.add((owner_id, source_record_id, external_key))

    actual = {
        (
            link.owner_code_node_id,
            link.external_source_record_id,
            link.external_key,
        )
        for link in resolved_links
    }
    if actual != linked:
        raise ExternalChunksProjectionError(
            "resolved links differ from linked external-reference occurrences: "
            f"missing={sorted(linked - actual)}, unexpected={sorted(actual - linked)}"
        )

    control = {
        "analysis_scope_id": analysis_scope_id,
        "generation_id": generation_id,
    }
    product_node_by_record_key: dict[tuple[str, str], NodeIdentity] = {}
    node_operations: list[NodeCreateOperation] = []
    relationship_operations: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    for _owner_id, record_id, external_key in sorted(linked):
        record_key = record_id, external_key
        if record_key in product_node_by_record_key:
            continue
        record = records[record_id]
        stable_id = published_external_chunk_id(
            record.source_file_id, external_key, record.content_sha256,
        )
        identity = NodeIdentity("ExternalChunk", stable_id)
        properties = {
            **control,
            "stable_id": stable_id,
            "external_key": external_key,
            "semantic_kind": record.semantic_kind,
            "source_file_id": record.source_file_id,
            "source_record_identity": record.source_record_id,
            "external_adapter_id": record.external_adapter_id,
            "external_adapter_version": record.external_adapter_version,
            "start_line": record.source_start_line,
            "end_line": record.source_end_line,
            "content_sha256": record.content_sha256,
            "exact_text": record.exact_text,
        }
        product_node_by_record_key[record_key] = identity
        node_operations.append(NodeCreateOperation.from_properties(
            identity, properties,
        ))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=EXTERNAL_CHUNK_PROPERTY_KEYS,
            present_properties=properties,
        ))

    for owner_id, record_id, external_key in sorted(linked):
        identity = RelationshipIdentity(
            "REFER_TO",
            NodeIdentity("CodeNode", owner_id),
            product_node_by_record_key[(record_id, external_key)],
        )
        relationship_operations.append(RelationshipCreateOperation.from_properties(
            identity, control,
        ))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=REFER_TO_PROPERTY_KEYS,
            present_properties=control,
        ))

    return ProductWriteBatch.build(
        stage_name="external_chunks",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=node_operations,
        relationship_create_operations=relationship_operations,
        expectations=expectations,
    )
