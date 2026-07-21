"""Deterministic per-code external evidence selection and bounded reconstruction."""
from __future__ import annotations

from collections import defaultdict
import hashlib

from graph.schema.graph_edges import CallsSql, Mentions, ReferTo, UsesConfig
from graph.schema.graph_nodes import CodeNode, DocumentNode, ExternalChunk
from pipeline.models.analysis_context import ExternalEvidenceContext
from pipeline.steps.step3_load_external.chunking import (
    ExternalChunkIntegrityError,
    reconstruct_external_chunk,
)


MAX_EXTERNAL_EVIDENCE_ITEMS = 8
MAX_EXTERNAL_EVIDENCE_CHARS = 6000


class ExternalEvidenceContractError(ValueError):
    """Raised when linked external evidence cannot be reconstructed faithfully."""


def build_external_evidence_context(
    state: dict,
    code_nodes: list[CodeNode],
    *,
    max_items: int = MAX_EXTERNAL_EVIDENCE_ITEMS,
    char_budget: int = MAX_EXTERNAL_EVIDENCE_CHARS,
) -> dict[str, tuple[ExternalEvidenceContext, ...]]:
    """Return only exact linked chunks for each code node, with stable bounds."""
    if max_items <= 0 or char_budget <= 0:
        raise ValueError("external evidence bounds must be positive")

    chunks: list[ExternalChunk] = state.get("external_chunks") or []
    anchors = {chunk.id: chunk for chunk in chunks if chunk.part_index == 0 and chunk.key}
    document_chunks: dict[str, DocumentNode] = {
        node.id: node
        for node in (state.get("document_nodes") or [])
        if node.label == "DOC_CHUNK" and node.text
    }
    parts_by_anchor: dict[str, list[ExternalChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.part_index:
            parts_by_anchor[chunk.anchor_id].append(chunk)

    code_ids = {node.id for node in code_nodes}
    code_ids_by_owner: dict[str, list[str]] = defaultdict(list)
    for node in code_nodes:
        if node.owner_id:
            code_ids_by_owner[node.owner_id].append(node.id)

    linked: dict[str, set[str]] = defaultdict(set)
    for edge in state.get("external_edges") or []:
        if isinstance(edge, (CallsSql, UsesConfig)):
            if edge.source_id in code_ids and edge.target_id in anchors:
                linked[edge.source_id].add(edge.target_id)
        elif isinstance(edge, ReferTo):
            if edge.source_id in code_ids and edge.target_id in anchors:
                linked[edge.source_id].add(edge.target_id)
            elif edge.source_id in anchors:
                for code_id in code_ids_by_owner.get(edge.target_id, ()):
                    linked[code_id].add(edge.source_id)
        elif isinstance(edge, Mentions) and edge.source_id in document_chunks and edge.target_id in code_ids:
            linked[edge.target_id].add(edge.source_id)

    result: dict[str, tuple[ExternalEvidenceContext, ...]] = {}
    for code_id, anchor_ids in linked.items():
        remaining = char_budget
        evidence: list[ExternalEvidenceContext] = []
        sources = [
            anchors[source_id] if source_id in anchors else document_chunks[source_id]
            for source_id in anchor_ids
            if source_id in anchors or source_id in document_chunks
        ]
        ordered = sorted(sources, key=lambda item: (
            item.file_path,
            item.start_line,
            getattr(item, "key", getattr(item, "title", "")),
            item.id,
        ))
        for anchor in ordered[:max_items]:
            if isinstance(anchor, ExternalChunk):
                try:
                    full_text = reconstruct_external_chunk(anchor, parts_by_anchor.get(anchor.id, []))
                except ExternalChunkIntegrityError as error:
                    raise ExternalEvidenceContractError(str(error)) from error
                key = anchor.key
                kind = anchor.kind
                full_len = anchor.full_len
            else:
                full_text = anchor.text
                actual_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
                if actual_hash != anchor.content_hash:
                    raise ExternalEvidenceContractError(
                        f"document chunk content hash mismatch: {anchor.id}"
                    )
                key = anchor.title or anchor.name
                kind = "document"
                full_len = len(full_text)
            metadata_chars = len(anchor.file_path) + len(key) + len(kind) + 96
            available = max(0, remaining - metadata_chars)
            if full_text and available == 0:
                break
            text = full_text[:available]
            evidence.append(ExternalEvidenceContext(
                id=anchor.id,
                file_path=anchor.file_path,
                key=key,
                kind=kind,
                text=text,
                start_line=anchor.start_line,
                end_line=anchor.end_line,
                content_hash=anchor.content_hash,
                full_len=full_len,
                truncated=len(text) < len(full_text),
            ))
            remaining -= metadata_chars + len(text)
            if remaining <= 0:
                break
        if evidence:
            result[code_id] = tuple(evidence)
    return result
