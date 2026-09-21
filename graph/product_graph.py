"""Small, DB-independent contract for the published Analyzer graph."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping


GRAPH_OWNER = "analyzer"

NODE_PROPERTY_KEYS: dict[str, frozenset[str]] = {
    "package": frozenset({"name", "qualified_name"}),
    "source": frozenset({
        "name", "file_path", "start_line", "end_line", "code_text",
        "signature", "summary", "stereotype", "_search_embedding",
        "data_type", "value",
    }),
    "table": frozenset({
        "datasource", "database", "schema", "name", "qualified_name",
        "logical_name", "table_type", "description", "summary",
    }),
    "column": frozenset({
        "name", "logical_name", "data_type", "nullable", "primary_key",
        "unique", "default_value", "description", "summary",
    }),
    "rule": frozenset({
        "line", "condition", "effects", "condition_description",
        "effect_descriptions", "code_text",
    }),
    "external": frozenset({
        "key", "kind", "file_path", "start_line", "end_line", "code_text",
    }),
}

RELATIONSHIP_PROPERTY_KEYS: dict[str, frozenset[str]] = {
    "PARENT_OF": frozenset(),
    "NEXT": frozenset(),
    "CALLS": frozenset(),
    "EXTENDS": frozenset(),
    "IMPLEMENTS": frozenset(),
    "REFERENCES": frozenset(),
    "INIT_BY": frozenset(),
    "READS": frozenset({"operations"}),
    "WRITES": frozenset({"operations"}),
    "HAS_COLUMN": frozenset(),
    "FK": frozenset({"from_column", "to_column"}),
    "HAS_RULE": frozenset({"order"}),
}

_LABEL = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ProductGraphError(ValueError):
    """The in-memory product graph is incomplete or outside the whitelist."""


def _clean_properties(values: Mapping[str, object]) -> dict[str, object]:
    """Drop absent optional values while retaining meaningful false and zero."""
    result: dict[str, object] = {}
    for key, value in values.items():
        if value is None or value == "" or value == () or value == []:
            continue
        if isinstance(value, tuple):
            value = list(value)
        result[str(key)] = value
    return result


def _relationship_key(
    relationship: "ProductRelationship",
) -> tuple[str, str, str, str, str]:
    """Use the column pair as part of FK identity; other edges stay endpoint-unique."""
    if relationship.relationship_type == "FK":
        return (
            relationship.relationship_type,
            relationship.source_id,
            relationship.target_id,
            str(relationship.properties.get("from_column") or ""),
            str(relationship.properties.get("to_column") or ""),
        )
    return (
        relationship.relationship_type,
        relationship.source_id,
        relationship.target_id,
        "",
        "",
    )


@dataclass(frozen=True)
class ProductNode:
    category: str
    label: str
    node_id: str
    properties: Mapping[str, object]

    @classmethod
    def build(
        cls,
        *,
        category: str,
        label: str,
        node_id: str,
        properties: Mapping[str, object],
    ) -> "ProductNode":
        normalized_label = str(label or "").strip().upper()
        normalized_id = str(node_id or "").strip()
        if category not in NODE_PROPERTY_KEYS:
            raise ProductGraphError(f"unknown node category: {category!r}")
        if not _LABEL.fullmatch(normalized_label):
            raise ProductGraphError(f"invalid node label: {label!r}")
        if not normalized_id:
            raise ProductGraphError("node _id must be non-blank")
        cleaned = _clean_properties(properties)
        unexpected = set(cleaned) - NODE_PROPERTY_KEYS[category]
        if unexpected:
            raise ProductGraphError(
                f"{normalized_label} has non-product properties: {sorted(unexpected)}"
            )
        if category in {"source", "rule", "external"} and not str(
            cleaned.get("code_text") or ""
        ).strip():
            raise ProductGraphError(
                f"source-backed node is missing code_text: {normalized_label}:{normalized_id}"
            )
        return cls(category, normalized_label, normalized_id, cleaned)

    def write_properties(self) -> dict[str, object]:
        return {"product_id": self.node_id, "_owner": GRAPH_OWNER, **self.properties}


@dataclass(frozen=True)
class ProductRelationship:
    relationship_type: str
    source_id: str
    target_id: str
    properties: Mapping[str, object]

    @classmethod
    def build(
        cls,
        relationship_type: str,
        source_id: str,
        target_id: str,
        properties: Mapping[str, object] | None = None,
    ) -> "ProductRelationship":
        rel_type = str(relationship_type or "").strip().upper()
        source = str(source_id or "").strip()
        target = str(target_id or "").strip()
        if rel_type not in RELATIONSHIP_PROPERTY_KEYS:
            raise ProductGraphError(f"unknown relationship type: {rel_type!r}")
        if not source or not target:
            raise ProductGraphError(f"{rel_type} endpoints must be non-blank")
        cleaned = _clean_properties(properties or {})
        unexpected = set(cleaned) - RELATIONSHIP_PROPERTY_KEYS[rel_type]
        if unexpected:
            raise ProductGraphError(
                f"{rel_type} has non-product properties: {sorted(unexpected)}"
            )
        return cls(rel_type, source, target, cleaned)

    def write_properties(self) -> dict[str, object]:
        return {"_owner": GRAPH_OWNER, **self.properties}


@dataclass(frozen=True)
class ProductGraph:
    nodes: tuple[ProductNode, ...]
    relationships: tuple[ProductRelationship, ...]
    sha256: str

    @classmethod
    def build(
        cls,
        *,
        nodes: Iterable[ProductNode],
        relationships: Iterable[ProductRelationship],
    ) -> "ProductGraph":
        ordered_nodes = tuple(sorted(nodes, key=lambda n: (n.node_id, n.label)))
        node_by_id: dict[str, ProductNode] = {}
        for node in ordered_nodes:
            previous = node_by_id.get(node.node_id)
            if previous is not None:
                raise ProductGraphError(
                    f"duplicate node _id: {node.node_id!r} ({previous.label}, {node.label})"
                )
            node_by_id[node.node_id] = node

        ordered_relationships = tuple(sorted(
            relationships,
            key=_relationship_key,
        ))
        seen_relationships: set[tuple[str, str, str, str, str]] = set()
        for relationship in ordered_relationships:
            key = _relationship_key(relationship)
            if key in seen_relationships:
                raise ProductGraphError(f"duplicate relationship: {key!r}")
            seen_relationships.add(key)
            missing = [
                endpoint for endpoint in (relationship.source_id, relationship.target_id)
                if endpoint not in node_by_id
            ]
            if missing:
                raise ProductGraphError(
                    f"{relationship.relationship_type} has missing endpoints: {missing}"
                )

        canonical = {
            "nodes": [
                [node.category, node.label, node.node_id, dict(node.properties)]
                for node in ordered_nodes
            ],
            "relationships": [
                [
                    rel.relationship_type,
                    rel.source_id,
                    rel.target_id,
                    dict(rel.properties),
                ]
                for rel in ordered_relationships
            ],
        }
        digest = hashlib.sha256(json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        return cls(ordered_nodes, ordered_relationships, digest)
