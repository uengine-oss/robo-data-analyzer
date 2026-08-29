"""Closed product projection for deterministic semantic-search segments."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping

from graph.product_write_batch import (
    NodeCreateOperation,
    NodeIdentity,
    ProductRecordExpectation,
    ProductWriteBatch,
    RelationshipCreateOperation,
    RelationshipIdentity,
)
from graph.publication_schema import PRODUCT_NODE_TYPES


SEARCH_SEGMENT_PROPERTY_KEYS = frozenset({
    "stable_id",
    "analysis_scope_id",
    "generation_id",
    "source_start_line",
    "source_end_line",
    "text_sha256",
    "text",
    "embedding_model_id",
    "embedding",
})
HAS_SEARCH_SEGMENT_PROPERTY_KEYS = frozenset({
    "analysis_scope_id",
    "generation_id",
})
_NON_PARENT_PRODUCT_TYPES = frozenset({"SearchSegment", "AnalysisHealth"})


class SearchSegmentsProjectionError(ValueError):
    """Search segment input is incomplete, duplicated, or not reproducible."""


def search_segment_stable_id(
    parent_product_type: str,
    parent_stable_id: str,
    ordinal: int,
    text_sha256: str,
) -> str:
    """Derive a delimiter-safe stable identity from parent, order, and text hash."""
    payload = json.dumps(
        [parent_product_type, parent_stable_id, ordinal, text_sha256],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "search-segment:" + hashlib.sha256(payload).hexdigest()


def _required_text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise SearchSegmentsProjectionError(f"search segment {key} must be non-blank")
    return value


def _optional_source_lines(record: Mapping[str, object]) -> dict[str, int]:
    start = record.get("source_start_line")
    end = record.get("source_end_line")
    if start is None and end is None:
        return {}
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start <= 0
        or end < start
    ):
        raise SearchSegmentsProjectionError(
            "search segment source lines must be both present and ordered"
        )
    return {"source_start_line": start, "source_end_line": end}


def build_search_segments_write_batch(
    *,
    analysis_scope_id: str,
    generation_id: str,
    segments: Iterable[Mapping[str, object]],
) -> ProductWriteBatch:
    """Create full-text/vector segments and their exact product parents.

    Each input record is an Analyzer-internal transport with parent identity and
    ordinal.  Those three routing values are represented by the relationship and
    stable ID, not duplicated as node properties.
    """
    nodes: list[NodeCreateOperation] = []
    relationships: list[RelationshipCreateOperation] = []
    expectations: list[ProductRecordExpectation] = []
    ordinals_by_parent: dict[tuple[str, str], list[int]] = defaultdict(list)
    seen_segment_ids: set[str] = set()

    normalized = []
    for record in segments:
        parent_type = _required_text(record, "parent_product_type")
        parent_id = _required_text(record, "parent_stable_id")
        if (
            parent_type not in PRODUCT_NODE_TYPES
            or parent_type in _NON_PARENT_PRODUCT_TYPES
        ):
            raise SearchSegmentsProjectionError(
                f"invalid search segment parent product type: {parent_type!r}"
            )
        ordinal = record.get("ordinal")
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal <= 0:
            raise SearchSegmentsProjectionError(
                "search segment ordinal must be a positive integer"
            )
        text = _required_text(record, "text")
        if text != text.strip():
            raise SearchSegmentsProjectionError(
                "search segment text must not have surrounding whitespace"
            )
        text_hash = _required_text(record, "text_sha256")
        actual_text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if text_hash != actual_text_hash:
            raise SearchSegmentsProjectionError(
                f"search segment text hash mismatch: {parent_type}:{parent_id}"
            )
        embedding_model_id = _required_text(record, "embedding_model_id")
        raw_embedding = record.get("embedding")
        if not isinstance(raw_embedding, (list, tuple)) or not raw_embedding:
            raise SearchSegmentsProjectionError(
                "search segment embedding must be a non-empty float vector"
            )
        embedding = tuple(raw_embedding)
        if any(
            isinstance(value, bool)
            or not isinstance(value, float)
            or not math.isfinite(value)
            for value in embedding
        ):
            raise SearchSegmentsProjectionError(
                "search segment embedding must contain only finite floats"
            )
        stable_id = _required_text(record, "stable_id")
        expected_id = search_segment_stable_id(
            parent_type, parent_id, ordinal, text_hash,
        )
        if stable_id != expected_id:
            raise SearchSegmentsProjectionError(
                f"search segment stable ID mismatch: expected={expected_id!r}"
            )
        if stable_id in seen_segment_ids:
            raise SearchSegmentsProjectionError(
                f"duplicate SearchSegment identity: {stable_id!r}"
            )
        seen_segment_ids.add(stable_id)
        ordinals_by_parent[parent_type, parent_id].append(ordinal)
        normalized.append((
            parent_type,
            parent_id,
            ordinal,
            stable_id,
            text,
            text_hash,
            embedding_model_id,
            embedding,
            _optional_source_lines(record),
        ))

    for parent, ordinals in ordinals_by_parent.items():
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise SearchSegmentsProjectionError(
                f"search segment ordinals are not contiguous for {parent!r}"
            )

    for (
        parent_type,
        parent_id,
        ordinal,
        stable_id,
        text,
        text_hash,
        embedding_model_id,
        embedding,
        source_lines,
    ) in sorted(normalized, key=lambda item: (item[0], item[1], item[2])):
        del ordinal
        identity = NodeIdentity("SearchSegment", stable_id)
        properties = {
            "stable_id": stable_id,
            "analysis_scope_id": analysis_scope_id,
            "generation_id": generation_id,
            "text_sha256": text_hash,
            "text": text,
            "embedding_model_id": embedding_model_id,
            "embedding": embedding,
            **source_lines,
        }
        nodes.append(NodeCreateOperation.from_properties(identity, properties))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=identity,
            closed_property_keys=SEARCH_SEGMENT_PROPERTY_KEYS,
            present_properties=properties,
        ))

        relationship_identity = RelationshipIdentity(
            "HAS_SEARCH_SEGMENT",
            NodeIdentity(parent_type, parent_id),
            identity,
        )
        relationship_properties = {
            "analysis_scope_id": analysis_scope_id,
            "generation_id": generation_id,
        }
        relationships.append(RelationshipCreateOperation.from_properties(
            relationship_identity,
            relationship_properties,
        ))
        expectations.append(ProductRecordExpectation.from_properties(
            identity=relationship_identity,
            closed_property_keys=HAS_SEARCH_SEGMENT_PROPERTY_KEYS,
            present_properties=relationship_properties,
        ))

    return ProductWriteBatch.build(
        stage_name="search_segments",
        analysis_scope_id=analysis_scope_id,
        generation_id=generation_id,
        node_create_operations=nodes,
        relationship_create_operations=relationships,
        expectations=expectations,
    )
