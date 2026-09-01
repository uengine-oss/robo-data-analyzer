"""Per-routine external evidence selection (spec 078).

Only the routine's outgoing external ``REFERENCES`` targets are selected. Every linked
chunk is re-verified against its stored hash and preserved whole.  Provider
input sizing belongs to the later semantic-obligation planner; this canonical
context owner never applies an item or character result ceiling.
"""
from __future__ import annotations

from collections import defaultdict

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import ExternalEvidenceContext
from pipeline.models.external_reference_resolution import ResolvedExternalReferenceLink
from pipeline.models.external_source import ExternalSourceRecord
from pipeline.stages.external.span_verifier import sha256_text


class ExternalEvidenceContractError(ValueError):
    """Raised when linked external evidence fails hash re-verification."""


def build_external_evidence_context(
    state: dict,
    code_nodes: list[CodeNode],
) -> dict[str, tuple[ExternalEvidenceContext, ...]]:
    """Return whole linked chunks for each routine, deterministically ordered."""
    records: dict[str, ExternalSourceRecord] = {
        record.source_record_id: record
        for record in (state.get("external_source_records") or [])
    }
    code_ids = {node.id for node in code_nodes}

    linked: dict[str, dict[str, str]] = defaultdict(dict)
    for link in state.get("resolved_external_reference_links") or []:
        if (
            isinstance(link, ResolvedExternalReferenceLink)
            and link.owner_code_node_id in code_ids
            and link.external_source_record_id in records
        ):
            linked[link.owner_code_node_id].setdefault(
                link.external_source_record_id, link.external_key,
            )

    result: dict[str, tuple[ExternalEvidenceContext, ...]] = {}
    for code_id, targets in linked.items():
        ordered = sorted(
            (records[record_id] for record_id in targets),
            key=lambda record: (
                record.project_relative_path,
                record.source_character_start,
                record.source_record_id,
            ),
        )
        evidence: list[ExternalEvidenceContext] = []
        for record in ordered:
            if sha256_text(record.exact_text) != record.content_sha256:
                raise ExternalEvidenceContractError(
                    "external source record content hash mismatch: "
                    f"{record.source_record_id}"
                )
            evidence.append(ExternalEvidenceContext(
                id=record.source_record_id,
                file_path=record.project_relative_path,
                matched_key=targets[record.source_record_id],
                semantic_kind=record.semantic_kind,
                provenance=record.provenance,
                text=record.exact_text,
                start_line=record.source_start_line,
                end_line=record.source_end_line,
                content_hash=record.content_sha256,
            ))
        if evidence:
            result[code_id] = tuple(evidence)
    return result
