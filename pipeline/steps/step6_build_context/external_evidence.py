"""Per-routine external evidence selection (spec 078).

Only the routine's outgoing ``REFER_TO`` targets are selected.  Every linked
chunk is re-verified against its stored hash and preserved whole.  Provider
input sizing belongs to the later semantic-obligation planner; this canonical
context owner never applies an item or character result ceiling.
"""
from __future__ import annotations

from collections import defaultdict

from graph.schema.graph_edges import ReferTo
from graph.schema.graph_nodes import CodeNode, ExternalChunk
from pipeline.models.analysis_context import ExternalEvidenceContext
from pipeline.steps.step3_load_external.span_verifier import sha256_text


class ExternalEvidenceContractError(ValueError):
    """Raised when linked external evidence fails hash re-verification."""


def build_external_evidence_context(
    state: dict,
    code_nodes: list[CodeNode],
) -> dict[str, tuple[ExternalEvidenceContext, ...]]:
    """Return whole linked chunks for each routine, deterministically ordered."""
    chunks: dict[str, ExternalChunk] = {
        chunk.id: chunk for chunk in (state.get("external_chunks") or [])
    }
    code_ids = {node.id for node in code_nodes}

    linked: dict[str, dict[str, str]] = defaultdict(dict)   # code_id -> chunk_id -> matched_key
    for edge in state.get("external_edges") or []:
        if isinstance(edge, ReferTo) and edge.source_id in code_ids and edge.target_id in chunks:
            linked[edge.source_id].setdefault(edge.target_id, edge.matched_key)

    result: dict[str, tuple[ExternalEvidenceContext, ...]] = {}
    for code_id, targets in linked.items():
        ordered = sorted(
            (chunks[chunk_id] for chunk_id in targets),
            key=lambda chunk: (chunk.file_path, chunk.char_start, chunk.id),
        )
        evidence: list[ExternalEvidenceContext] = []
        for chunk in ordered:
            if sha256_text(chunk.text) != chunk.content_hash:
                raise ExternalEvidenceContractError(
                    f"external chunk content hash mismatch: {chunk.id}")
            evidence.append(ExternalEvidenceContext(
                id=chunk.id,
                file_path=chunk.file_path,
                matched_key=targets[chunk.id],
                semantic_kind=chunk.semantic_kind,
                provenance=chunk.provenance,
                text=chunk.text,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content_hash=chunk.content_hash,
            ))
        if evidence:
            result[code_id] = tuple(evidence)
    return result
