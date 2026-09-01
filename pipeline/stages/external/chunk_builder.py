"""Build verified external-source records from adapter output."""
from __future__ import annotations

from graph.schema.node_ids import derived_chunk_id, external_chunk_id
from pipeline.models.external_source import ExternalSourceRecord
from pipeline.stages.external.span_verifier import VerifiedChunk, sha256_text


def build_source_records(
    *,
    file_id: str,
    relpath: str,
    source: str,
    verified: list[VerifiedChunk],
    external_adapter_id: str,
    external_adapter_version: str,
) -> list[ExternalSourceRecord]:
    source_sha256 = sha256_text(source)
    records: list[ExternalSourceRecord] = []
    for chunk in verified:
        record_id = external_chunk_id(file_id, chunk.char_start, chunk.char_end)
        records.append(ExternalSourceRecord(
            source_record_id=record_id,
            source_file_id=file_id,
            external_adapter_id=external_adapter_id,
            external_adapter_version=external_adapter_version,
            addressable_keys=chunk.addressable_keys,
            semantic_kind=chunk.semantic_kind,
            exact_text=chunk.text,
            project_relative_path=relpath,
            source_character_start=chunk.char_start,
            source_character_end=chunk.char_end,
            source_start_line=chunk.start_line,
            source_end_line=chunk.end_line,
            provenance="source_span",
            source_sha256=source_sha256,
            content_sha256=chunk.content_hash,
        ))
    return records


def build_derived_records(
    *,
    file_id: str,
    relpath: str,
    segments: list[str],
    external_adapter_id: str,
    external_adapter_version: str,
) -> list[ExternalSourceRecord]:
    records: list[ExternalSourceRecord] = []
    ordinal = 0
    for segment in segments:
        if not segment.strip():
            continue
        record_id = derived_chunk_id(file_id, ordinal)
        records.append(ExternalSourceRecord(
            source_record_id=record_id,
            source_file_id=file_id,
            external_adapter_id=external_adapter_id,
            external_adapter_version=external_adapter_version,
            addressable_keys=(),
            semantic_kind="derived_document",
            exact_text=segment,
            project_relative_path=relpath,
            source_character_start=0,
            source_character_end=0,
            source_start_line=0,
            source_end_line=0,
            provenance="derived_document",
            source_sha256="",
            content_sha256=sha256_text(segment),
        ))
        ordinal += 1
    return records
