"""DB-independent product write plans and canonical publication hashes.

Pipeline stages describe the graph records they intend to publish here before
opening a Neo4j transaction.  This module owns no driver and executes no query.
It closes three failure modes that direct ``SET`` calls cannot detect:

* one operation silently disappearing or being executed twice;
* two stages owning the same ``(product identity, property key)``;
* Neo4j containing a value or an unexpected property different from the
  immutable pre-write expectation.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, TypeAlias

from graph.publication_schema import (
    PRODUCT_NODE_TYPES,
    PRODUCT_RELATIONSHIP_TYPES,
    PRODUCT_STAGE_NAMES,
)


CANONICAL_PRODUCT_RECORD_HASH_SCHEME = "canonical-product-record-hash/v2"

Primitive: TypeAlias = bool | int | float | str
PropertyValue: TypeAlias = Primitive | tuple[Primitive, ...]
OperationKind: TypeAlias = Literal[
    "node_create", "node_property_set", "relationship_create",
    "relationship_property_set",
]


class ProductWriteContractError(ValueError):
    """A DB-independent product write plan violates the publication contract."""


def _require_nonblank(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductWriteContractError(f"{name} must be a non-blank string")
    return value


def _normalize_property_value(
    value: object,
    *,
    identity: str,
    property_key: str,
) -> PropertyValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProductWriteContractError(
                f"non-finite float is not a Neo4j product property: "
                f"{identity}.{property_key}"
            )
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        normalized = tuple(
            _normalize_property_value(
                item,
                identity=identity,
                property_key=f"{property_key}[]",
            )
            for item in value
        )
        if any(isinstance(item, tuple) for item in normalized):
            raise ProductWriteContractError(
                f"nested property list is forbidden: {identity}.{property_key}"
            )
        item_types = {type(item) for item in normalized}
        if len(item_types) > 1:
            raise ProductWriteContractError(
                f"heterogeneous property list is forbidden: "
                f"{identity}.{property_key}"
            )
        return normalized
    raise ProductWriteContractError(
        f"unsupported product property value: {identity}.{property_key}"
    )


def _normalize_properties(
    identity: str,
    properties: Mapping[str, object],
) -> tuple[tuple[str, PropertyValue], ...]:
    normalized: list[tuple[str, PropertyValue]] = []
    for raw_key, raw_value in properties.items():
        key = _require_nonblank("product property key", raw_key)
        normalized.append((
            key,
            _normalize_property_value(
                raw_value,
                identity=identity,
                property_key=key,
            ),
        ))
    keys = [key for key, _ in normalized]
    if len(keys) != len(set(keys)):
        raise ProductWriteContractError(
            f"duplicate product property key: {identity}"
        )
    return tuple(sorted(normalized))


@dataclass(frozen=True, order=True)
class NodeIdentity:
    """Canonical identity of one generation-owned product node."""

    product_type: str
    stable_id: str

    def __post_init__(self) -> None:
        if self.product_type not in PRODUCT_NODE_TYPES:
            raise ProductWriteContractError(
                f"unknown product node type: {self.product_type!r}"
            )
        _require_nonblank("node stable_id", self.stable_id)

    def canonical_key(self) -> str:
        return f"node\x1f{self.product_type}\x1f{self.stable_id}"


@dataclass(frozen=True, order=True)
class RelationshipIdentity:
    """Canonical identity of one deduplicated product relationship."""

    relationship_type: str
    source: NodeIdentity
    target: NodeIdentity

    def __post_init__(self) -> None:
        if self.relationship_type not in PRODUCT_RELATIONSHIP_TYPES:
            raise ProductWriteContractError(
                f"unknown product relationship type: {self.relationship_type!r}"
            )

    def canonical_key(self) -> str:
        return (
            f"relationship\x1f{self.relationship_type}\x1f"
            f"{self.source.canonical_key()}\x1f{self.target.canonical_key()}"
        )


ProductIdentity: TypeAlias = NodeIdentity | RelationshipIdentity


@dataclass(frozen=True)
class NodeCreateOperation:
    identity: NodeIdentity
    present_properties: tuple[tuple[str, PropertyValue], ...]

    @classmethod
    def from_properties(
        cls,
        identity: NodeIdentity,
        properties: Mapping[str, object],
    ) -> "NodeCreateOperation":
        return cls(
            identity=identity,
            present_properties=_normalize_properties(
                identity.canonical_key(), properties,
            ),
        )


@dataclass(frozen=True)
class NodePropertySetOperation:
    identity: NodeIdentity
    property_key: str
    value: PropertyValue

    @classmethod
    def from_value(
        cls,
        identity: NodeIdentity,
        property_key: str,
        value: object,
    ) -> "NodePropertySetOperation":
        key = _require_nonblank("product property key", property_key)
        return cls(
            identity=identity,
            property_key=key,
            value=_normalize_property_value(
                value,
                identity=identity.canonical_key(),
                property_key=key,
            ),
        )


@dataclass(frozen=True)
class RelationshipCreateOperation:
    identity: RelationshipIdentity
    present_properties: tuple[tuple[str, PropertyValue], ...]

    @classmethod
    def from_properties(
        cls,
        identity: RelationshipIdentity,
        properties: Mapping[str, object],
    ) -> "RelationshipCreateOperation":
        return cls(
            identity=identity,
            present_properties=_normalize_properties(
                identity.canonical_key(), properties,
            ),
        )


@dataclass(frozen=True)
class RelationshipPropertySetOperation:
    """Set one property owned by a later stage on an existing relationship."""

    identity: RelationshipIdentity
    property_key: str
    value: PropertyValue

    @classmethod
    def from_value(
        cls,
        identity: RelationshipIdentity,
        property_key: str,
        value: object,
    ) -> "RelationshipPropertySetOperation":
        key = _require_nonblank("product property key", property_key)
        return cls(
            identity=identity,
            property_key=key,
            value=_normalize_property_value(
                value,
                identity=identity.canonical_key(),
                property_key=key,
            ),
        )


WriteOperation: TypeAlias = (
    NodeCreateOperation
    | NodePropertySetOperation
    | RelationshipCreateOperation
    | RelationshipPropertySetOperation
)


@dataclass(frozen=True)
class ProductRecordExpectation:
    """One stage's closed property slice for one product identity."""

    identity: ProductIdentity
    closed_property_keys: tuple[str, ...]
    present_properties: tuple[tuple[str, PropertyValue], ...]

    @classmethod
    def from_properties(
        cls,
        *,
        identity: ProductIdentity,
        closed_property_keys: Iterable[str],
        present_properties: Mapping[str, object],
    ) -> "ProductRecordExpectation":
        keys = tuple(sorted(
            _require_nonblank("closed product property key", key)
            for key in closed_property_keys
        ))
        if len(keys) != len(set(keys)):
            raise ProductWriteContractError(
                f"duplicate closed property key: {identity.canonical_key()}"
            )
        properties = _normalize_properties(
            identity.canonical_key(), present_properties,
        )
        unexpected = sorted(set(dict(properties)) - set(keys))
        if unexpected:
            raise ProductWriteContractError(
                f"present properties are outside the closed schema for "
                f"{identity.canonical_key()}: {unexpected}"
            )
        return cls(identity, keys, properties)

    @property
    def property_map(self) -> dict[str, PropertyValue]:
        return dict(self.present_properties)


def _framed(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _primitive_type(value: Primitive) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float64"
    return "string"


def _primitive_bytes(value: Primitive) -> bytes:
    value_type = _primitive_type(value).encode("ascii")
    if isinstance(value, bool):
        body = b"1" if value else b"0"
    elif isinstance(value, int):
        body = str(value).encode("ascii")
    elif isinstance(value, float):
        body = struct.pack(">d", value)
    else:
        body = value.encode("utf-8")
    return _framed(value_type) + _framed(body)


def _property_bytes(
    key: str,
    value: PropertyValue,
    *,
    properties: Mapping[str, PropertyValue],
) -> bytes:
    if key == "embedding":
        model_id = properties.get("embedding_model_id")
        if not isinstance(model_id, str) or not model_id:
            raise ProductWriteContractError(
                "embedding requires a present non-blank embedding_model_id"
            )
        if not isinstance(value, tuple) or any(
            not isinstance(item, float) for item in value
        ):
            raise ProductWriteContractError(
                "embedding must be a homogeneous finite-float list"
            )
        if not value:
            raise ProductWriteContractError("embedding vector must not be empty")
        body = _framed(model_id.encode("utf-8"))
        body += len(value).to_bytes(8, "big")
        body += b"".join(struct.pack(">d", item) for item in value)
        return _framed(b"embedding") + _framed(body)
    if isinstance(value, tuple):
        if not value:
            return _framed(b"list:empty") + (0).to_bytes(8, "big")
        item_type = _primitive_type(value[0])
        body = len(value).to_bytes(8, "big")
        body += b"".join(_primitive_bytes(item) for item in value)
        return _framed(f"list:{item_type}".encode("ascii")) + _framed(body)
    return _primitive_bytes(value)


def canonical_product_record_hash_v2(
    expectation: ProductRecordExpectation,
) -> str:
    """Hash identity, closed schema, presence, exact primitive types and values."""
    identity = expectation.identity
    properties = expectation.property_map
    payload = _framed(CANONICAL_PRODUCT_RECORD_HASH_SCHEME.encode("ascii"))
    payload += _framed(identity.canonical_key().encode("utf-8"))
    payload += len(expectation.closed_property_keys).to_bytes(8, "big")
    for key in expectation.closed_property_keys:
        payload += _framed(key.encode("utf-8"))
        if key not in properties:
            payload += _framed(b"absent")
            continue
        payload += _framed(b"present")
        payload += _property_bytes(key, properties[key], properties=properties)
    return hashlib.sha256(payload).hexdigest()


def _operation_kind(operation: WriteOperation) -> OperationKind:
    if isinstance(operation, NodeCreateOperation):
        return "node_create"
    if isinstance(operation, NodePropertySetOperation):
        return "node_property_set"
    if isinstance(operation, RelationshipPropertySetOperation):
        return "relationship_property_set"
    return "relationship_create"


def _operation_key(stage_name: str, operation: WriteOperation) -> tuple[str, ...]:
    identity_key = operation.identity.canonical_key()
    kind = _operation_kind(operation)
    if isinstance(operation, (NodePropertySetOperation, RelationshipPropertySetOperation)):
        return stage_name, kind, identity_key, operation.property_key
    return stage_name, kind, identity_key


def canonical_operation_key(operation_key: tuple[str, ...]) -> str:
    """Serialize one operation key without delimiter ambiguity."""
    return json.dumps(
        operation_key,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _artifact_value(value: PropertyValue) -> Primitive | list[Primitive]:
    return list(value) if isinstance(value, tuple) else value


@dataclass(frozen=True)
class ProductWriteBatch:
    """One product stage's immutable, DB-independent write obligation."""

    stage_name: str
    analysis_scope_id: str
    generation_id: str
    node_create_operations: tuple[NodeCreateOperation, ...]
    node_property_set_operations: tuple[NodePropertySetOperation, ...]
    relationship_create_operations: tuple[RelationshipCreateOperation, ...]
    relationship_property_set_operations: tuple[RelationshipPropertySetOperation, ...]
    expectations: tuple[ProductRecordExpectation, ...]
    required_operation_key_set: frozenset[tuple[str, ...]]
    required_property_key_set_by_identity: tuple[tuple[str, tuple[str, ...]], ...]
    required_product_record_hash_by_identity: tuple[tuple[str, str], ...]

    @property
    def canonical_batch_sha256(self) -> str:
        """Hash the complete immutable obligation used for idempotent retries."""
        payload = {
            "stage_name": self.stage_name,
            "analysis_scope_id": self.analysis_scope_id,
            "generation_id": self.generation_id,
            "required_operation_keys": sorted(
                canonical_operation_key(key)
                for key in self.required_operation_key_set
            ),
            "required_property_keys_by_identity": dict(
                self.required_property_key_set_by_identity
            ),
            "required_product_record_hash_by_identity": dict(
                self.required_product_record_hash_by_identity
            ),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def artifact_dict(self) -> dict[str, object]:
        """Return the content-addressable run-artifact representation."""
        def properties(
            pairs: tuple[tuple[str, PropertyValue], ...],
        ) -> dict[str, object]:
            return {key: _artifact_value(value) for key, value in pairs}

        return {
            "schema_version": "product-write-batch/v1",
            "stage_name": self.stage_name,
            "analysis_scope_id": self.analysis_scope_id,
            "generation_id": self.generation_id,
            "node_create_operations": [
                {
                    "operation_key": list(_operation_key(self.stage_name, item)),
                    "identity": item.identity.canonical_key(),
                    "present_properties": properties(item.present_properties),
                }
                for item in self.node_create_operations
            ],
            "node_property_set_operations": [
                {
                    "operation_key": list(_operation_key(self.stage_name, item)),
                    "identity": item.identity.canonical_key(),
                    "property_key": item.property_key,
                    "value": _artifact_value(item.value),
                }
                for item in self.node_property_set_operations
            ],
            "relationship_create_operations": [
                {
                    "operation_key": list(_operation_key(self.stage_name, item)),
                    "identity": item.identity.canonical_key(),
                    "present_properties": properties(item.present_properties),
                }
                for item in self.relationship_create_operations
            ],
            "relationship_property_set_operations": [
                {
                    "operation_key": list(_operation_key(self.stage_name, item)),
                    "identity": item.identity.canonical_key(),
                    "property_key": item.property_key,
                    "value": _artifact_value(item.value),
                }
                for item in self.relationship_property_set_operations
            ],
            "required_operation_key_set": [
                list(key) for key in sorted(self.required_operation_key_set)
            ],
            "required_property_key_set_by_identity": {
                identity: list(keys)
                for identity, keys in self.required_property_key_set_by_identity
            },
            "required_product_record_hash_by_identity": dict(
                self.required_product_record_hash_by_identity
            ),
            "canonical_batch_sha256": self.canonical_batch_sha256,
        }

    @classmethod
    def build(
        cls,
        *,
        stage_name: str,
        analysis_scope_id: str,
        generation_id: str,
        node_create_operations: Iterable[NodeCreateOperation] = (),
        node_property_set_operations: Iterable[NodePropertySetOperation] = (),
        relationship_create_operations: Iterable[RelationshipCreateOperation] = (),
        relationship_property_set_operations: Iterable[
            RelationshipPropertySetOperation
        ] = (),
        expectations: Iterable[ProductRecordExpectation] = (),
    ) -> "ProductWriteBatch":
        if stage_name not in PRODUCT_STAGE_NAMES:
            raise ProductWriteContractError(
                f"unknown product stage name: {stage_name!r}"
            )
        _require_nonblank("analysis_scope_id", analysis_scope_id)
        _require_nonblank("generation_id", generation_id)
        creates = tuple(node_create_operations)
        patches = tuple(node_property_set_operations)
        relationships = tuple(relationship_create_operations)
        relationship_patches = tuple(relationship_property_set_operations)
        records = tuple(expectations)
        operations: tuple[WriteOperation, ...] = (
            creates + patches + relationships + relationship_patches
        )

        for operation in creates:
            properties = dict(operation.present_properties)
            required_identity_properties = {
                "stable_id": operation.identity.stable_id,
                "analysis_scope_id": analysis_scope_id,
                "generation_id": generation_id,
            }
            if any(
                properties.get(key) != value
                for key, value in required_identity_properties.items()
            ):
                raise ProductWriteContractError(
                    f"node create is missing exact identity/control properties: "
                    f"{operation.identity.canonical_key()}"
                )
        for operation in relationships:
            properties = dict(operation.present_properties)
            if (
                properties.get("analysis_scope_id") != analysis_scope_id
                or properties.get("generation_id") != generation_id
            ):
                raise ProductWriteContractError(
                    f"relationship create is missing exact control properties: "
                    f"{operation.identity.canonical_key()}"
                )
        for operation in (*patches, *relationship_patches):
            if operation.property_key in {
                "stable_id", "analysis_scope_id", "generation_id",
            }:
                raise ProductWriteContractError(
                    f"identity/control property cannot be patched: "
                    f"{operation.identity.canonical_key()}.{operation.property_key}"
                )

        operation_keys = tuple(_operation_key(stage_name, item) for item in operations)
        if len(operation_keys) != len(set(operation_keys)):
            raise ProductWriteContractError(
                f"duplicate operation key in stage {stage_name!r}"
            )

        expectation_by_identity = {
            item.identity.canonical_key(): item for item in records
        }
        if len(expectation_by_identity) != len(records):
            raise ProductWriteContractError(
                f"duplicate product expectation in stage {stage_name!r}"
            )

        written_by_identity: dict[str, dict[str, PropertyValue]] = {}
        for operation in operations:
            identity_key = operation.identity.canonical_key()
            properties = written_by_identity.setdefault(identity_key, {})
            if isinstance(operation, (NodePropertySetOperation, RelationshipPropertySetOperation)):
                pairs = ((operation.property_key, operation.value),)
            else:
                pairs = operation.present_properties
            for key, value in pairs:
                if key in properties:
                    raise ProductWriteContractError(
                        f"property has multiple operations in stage {stage_name!r}: "
                        f"{identity_key}.{key}"
                    )
                properties[key] = value

        if set(written_by_identity) != set(expectation_by_identity):
            raise ProductWriteContractError(
                f"operation identities and expected identities differ in stage "
                f"{stage_name!r}"
            )
        for identity_key, written in written_by_identity.items():
            expected = expectation_by_identity[identity_key].property_map
            if written != expected:
                raise ProductWriteContractError(
                    f"operation properties differ from immutable expectation: "
                    f"{identity_key}"
                )

        property_keys = tuple(sorted(
            (identity, tuple(sorted(properties)))
            for identity, properties in written_by_identity.items()
        ))
        record_hashes = tuple(sorted(
            (
                identity,
                canonical_product_record_hash_v2(expectation),
            )
            for identity, expectation in expectation_by_identity.items()
        ))
        return cls(
            stage_name=stage_name,
            analysis_scope_id=analysis_scope_id,
            generation_id=generation_id,
            node_create_operations=creates,
            node_property_set_operations=patches,
            relationship_create_operations=relationships,
            relationship_property_set_operations=relationship_patches,
            expectations=records,
            required_operation_key_set=frozenset(operation_keys),
            required_property_key_set_by_identity=property_keys,
            required_product_record_hash_by_identity=record_hashes,
        )


@dataclass(frozen=True)
class ProductWritePlan:
    """Validated union of disjoint stage batches for one building generation."""

    analysis_scope_id: str
    generation_id: str
    batches: tuple[ProductWriteBatch, ...]
    expectations: tuple[ProductRecordExpectation, ...]
    required_operation_key_set: frozenset[tuple[str, ...]]
    property_owner_by_identity_and_key: tuple[tuple[str, str, str], ...]
    required_property_key_set_by_identity: tuple[tuple[str, tuple[str, ...]], ...]
    required_product_record_hash_by_identity: tuple[tuple[str, str], ...]


def combine_product_write_batches(
    batches: Iterable[ProductWriteBatch],
    *,
    analysis_scope_id: str,
    generation_id: str,
) -> ProductWritePlan:
    """Close operation, property-owner, endpoint and final-record obligations."""
    batch_tuple = tuple(batches)
    stage_names = [batch.stage_name for batch in batch_tuple]
    if len(stage_names) != len(set(stage_names)):
        raise ProductWriteContractError("one product stage emitted multiple batches")

    operation_keys: set[tuple[str, ...]] = set()
    owners: dict[tuple[str, str], str] = {}
    schema_by_identity: dict[str, set[str]] = {}
    properties_by_identity: dict[str, dict[str, PropertyValue]] = {}
    identity_objects: dict[str, ProductIdentity] = {}
    created_nodes: set[NodeIdentity] = set()
    patched_nodes: set[NodeIdentity] = set()
    created_relationships: set[RelationshipIdentity] = set()
    patched_relationships: set[RelationshipIdentity] = set()

    for batch in batch_tuple:
        if (
            batch.analysis_scope_id != analysis_scope_id
            or batch.generation_id != generation_id
        ):
            raise ProductWriteContractError(
                f"batch scope/generation mismatch: {batch.stage_name}"
            )
        overlap = operation_keys & batch.required_operation_key_set
        if overlap:
            raise ProductWriteContractError(
                f"operation keys overlap across product stages: {sorted(overlap)}"
            )
        operation_keys.update(batch.required_operation_key_set)

        created_nodes.update(item.identity for item in batch.node_create_operations)
        patched_nodes.update(
            item.identity for item in batch.node_property_set_operations
        )
        batch_created_relationships = {
            item.identity for item in batch.relationship_create_operations
        }
        if created_relationships & batch_created_relationships:
            raise ProductWriteContractError(
                "a product relationship identity is created by more than one stage"
            )
        created_relationships.update(batch_created_relationships)
        patched_relationships.update(
            item.identity for item in batch.relationship_property_set_operations
        )
        for expectation in batch.expectations:
            identity_key = expectation.identity.canonical_key()
            previous_identity = identity_objects.setdefault(
                identity_key, expectation.identity,
            )
            if previous_identity != expectation.identity:
                raise ProductWriteContractError(
                    f"canonical identity collision: {identity_key}"
                )
            schema = schema_by_identity.setdefault(identity_key, set())
            properties = properties_by_identity.setdefault(identity_key, {})
            for key in expectation.closed_property_keys:
                owner_key = identity_key, key
                previous_owner = owners.get(owner_key)
                if previous_owner is not None:
                    raise ProductWriteContractError(
                        f"property owner conflict: {identity_key}.{key} is owned by "
                        f"{previous_owner!r} and {batch.stage_name!r}"
                    )
                owners[owner_key] = batch.stage_name
                schema.add(key)
            for key, value in expectation.present_properties:
                properties[key] = value

    if len(created_nodes) != sum(
        len(batch.node_create_operations) for batch in batch_tuple
    ):
        raise ProductWriteContractError(
            "a product node identity is created by more than one stage"
        )
    missing_patch_targets = sorted(patched_nodes - created_nodes)
    if missing_patch_targets:
        raise ProductWriteContractError(
            "node property patch target is outside the product plan: "
            f"{missing_patch_targets[0].canonical_key()}"
        )
    missing_relationship_patch_targets = sorted(
        patched_relationships - created_relationships,
        key=lambda item: item.canonical_key(),
    )
    if missing_relationship_patch_targets:
        raise ProductWriteContractError(
            "relationship property patch target is outside the product plan: "
            f"{missing_relationship_patch_targets[0].canonical_key()}"
        )
    for relationship in created_relationships:
        if relationship.source not in created_nodes:
            raise ProductWriteContractError(
                f"relationship source is outside the product plan: "
                f"{relationship.canonical_key()}"
            )
        if relationship.target not in created_nodes:
            raise ProductWriteContractError(
                f"relationship target is outside the product plan: "
                f"{relationship.canonical_key()}"
            )

    final_expectations = []
    for identity_key, identity in identity_objects.items():
        final_expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=schema_by_identity[identity_key],
            present_properties=properties_by_identity[identity_key],
        ))

    return ProductWritePlan(
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        # The assembler owns dependency order: create endpoint nodes before
        # relationship stages and create relationships before semantic patches.
        # Alphabetical sorting is deterministic but invalid for execution
        # (for example, call_links would run before code_structure).
        batches=batch_tuple,
        expectations=tuple(sorted(
            final_expectations,
            key=lambda item: item.identity.canonical_key(),
        )),
        required_operation_key_set=frozenset(operation_keys),
        property_owner_by_identity_and_key=tuple(sorted(
            (identity, key, owner) for (identity, key), owner in owners.items()
        )),
        required_property_key_set_by_identity=tuple(sorted(
            (identity, tuple(sorted(properties_by_identity[identity])))
            for identity in schema_by_identity
        )),
        required_product_record_hash_by_identity=tuple(sorted(
            (
                item.identity.canonical_key(),
                canonical_product_record_hash_v2(item),
            )
            for item in final_expectations
        )),
    )


def verify_product_write_batch_readback(
    batch: ProductWriteBatch,
    actual_records: Iterable[tuple[ProductIdentity, Mapping[str, object]]],
) -> tuple[tuple[str, str], ...]:
    """Verify same-transaction DB read-back against one immutable stage batch.

    A node can already contain properties owned by earlier stages.  The check
    therefore projects only this stage's closed property slice, while still
    enforcing presence versus absence for every key in that slice.
    """
    actual_by_identity: dict[str, tuple[ProductIdentity, Mapping[str, object]]] = {}
    for identity, properties in actual_records:
        identity_key = identity.canonical_key()
        if identity_key in actual_by_identity:
            raise ProductWriteContractError(
                f"duplicate product read-back identity: {identity_key}"
            )
        actual_by_identity[identity_key] = identity, properties

    expected_by_identity = {
        expectation.identity.canonical_key(): expectation
        for expectation in batch.expectations
    }
    if set(actual_by_identity) != set(expected_by_identity):
        missing = sorted(set(expected_by_identity) - set(actual_by_identity))
        unexpected = sorted(set(actual_by_identity) - set(expected_by_identity))
        raise ProductWriteContractError(
            f"product read-back identity mismatch for {batch.stage_name!r}: "
            f"missing={missing}, unexpected={unexpected}"
        )

    actual_hashes: list[tuple[str, str]] = []
    expected_hashes = dict(batch.required_product_record_hash_by_identity)
    expected_present_keys = dict(batch.required_property_key_set_by_identity)
    for identity_key, expectation in expected_by_identity.items():
        identity, raw_properties = actual_by_identity[identity_key]
        closed_keys = set(expectation.closed_property_keys)
        projected = {
            key: value
            for key, value in raw_properties.items()
            if key in closed_keys
        }
        actual = ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=expectation.closed_property_keys,
            present_properties=projected,
        )
        actual_present_keys = tuple(sorted(actual.property_map))
        if actual_present_keys != expected_present_keys[identity_key]:
            raise ProductWriteContractError(
                f"product read-back property presence mismatch: {identity_key}"
            )
        actual_hash = canonical_product_record_hash_v2(actual)
        if actual_hash != expected_hashes[identity_key]:
            raise ProductWriteContractError(
                f"product read-back property value mismatch: {identity_key}"
            )
        actual_hashes.append((identity_key, actual_hash))
    return tuple(sorted(actual_hashes))


def verify_product_write_plan_readback(
    plan: ProductWritePlan,
    actual_records: Iterable[tuple[ProductIdentity, Mapping[str, object]]],
) -> tuple[tuple[str, str], ...]:
    """Verify the final graph has exactly the planned identities and properties."""
    actual_by_identity: dict[str, tuple[ProductIdentity, Mapping[str, object]]] = {}
    for identity, properties in actual_records:
        identity_key = identity.canonical_key()
        if identity_key in actual_by_identity:
            raise ProductWriteContractError(
                f"duplicate final product identity: {identity_key}"
            )
        actual_by_identity[identity_key] = identity, properties
    expected_by_identity = {
        item.identity.canonical_key(): item for item in plan.expectations
    }
    if set(actual_by_identity) != set(expected_by_identity):
        missing = sorted(set(expected_by_identity) - set(actual_by_identity))
        unexpected = sorted(set(actual_by_identity) - set(expected_by_identity))
        raise ProductWriteContractError(
            f"final product identity mismatch: missing={missing}, "
            f"unexpected={unexpected}"
        )

    required_present = dict(plan.required_property_key_set_by_identity)
    required_hashes = dict(plan.required_product_record_hash_by_identity)
    actual_hashes: list[tuple[str, str]] = []
    for identity_key, expectation in expected_by_identity.items():
        identity, properties = actual_by_identity[identity_key]
        actual_keys = tuple(sorted(properties))
        if actual_keys != required_present[identity_key]:
            raise ProductWriteContractError(
                f"final product property closure mismatch: {identity_key}"
            )
        actual = ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=expectation.closed_property_keys,
            present_properties=properties,
        )
        actual_hash = canonical_product_record_hash_v2(actual)
        if actual_hash != required_hashes[identity_key]:
            raise ProductWriteContractError(
                f"final product record hash mismatch: {identity_key}"
            )
        actual_hashes.append((identity_key, actual_hash))
    return tuple(sorted(actual_hashes))
