"""VerifiedChunk → graph ExternalChunk/HasChunk assembly (spec 078).

The only place external chunk nodes are constructed.  ``source_span`` chunks
carry the decoded source hash so ``text == source[char_start:char_end]`` stays
re-checkable anywhere; ``derived_document`` chunks (binary conversions) carry
no span claim and are excluded from direct linking by contract.
"""
from __future__ import annotations

from graph.schema.graph_edges import HasChunk
from graph.schema.graph_nodes import ExternalChunk
from graph.schema.node_ids import derived_chunk_id, external_chunk_id
from pipeline.steps.step3_load_external.span_verifier import VerifiedChunk, sha256_text


def build_source_chunks(
    *,
    file_id: str,
    relpath: str,
    source: str,
    verified: list[VerifiedChunk],
) -> tuple[list[ExternalChunk], list[HasChunk]]:
    source_hash = sha256_text(source)
    nodes: list[ExternalChunk] = []
    edges: list[HasChunk] = []
    for chunk in verified:
        node = ExternalChunk(
            id=external_chunk_id(file_id, chunk.char_start, chunk.char_end),
            name=chunk.name or f"{relpath}:{chunk.start_line}",
            keys=chunk.keys,
            semantic_kind=chunk.semantic_kind,
            text=chunk.text,
            file_path=relpath,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            provenance="source_span",
            source_hash=source_hash,
            content_hash=chunk.content_hash,
            addressable_keys=chunk.addressable_keys,
        )
        nodes.append(node)
        edges.append(HasChunk(source_id=file_id, target_id=node.id))
    return nodes, edges


def build_derived_chunks(
    *,
    file_id: str,
    relpath: str,
    segments: list[str],
) -> tuple[list[ExternalChunk], list[HasChunk]]:
    """Chunks from converted documents: honest provenance, no span claim."""
    nodes: list[ExternalChunk] = []
    edges: list[HasChunk] = []
    ordinal = 0
    for segment in segments:
        if not segment.strip():
            continue
        node = ExternalChunk(
            id=derived_chunk_id(file_id, ordinal),
            name=f"{relpath}#derived-{ordinal}",
            keys=(),
            semantic_kind="derived_document",
            text=segment,
            file_path=relpath,
            char_start=0,
            char_end=0,
            start_line=0,
            end_line=0,
            provenance="derived_document",
            source_hash="",
            content_hash=sha256_text(segment),
        )
        nodes.append(node)
        edges.append(HasChunk(source_id=file_id, target_id=node.id))
        ordinal += 1
    return nodes, edges
