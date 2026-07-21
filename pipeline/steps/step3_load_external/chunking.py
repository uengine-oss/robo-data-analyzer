"""Bounded external chunks with stable file-scoped identities."""
from __future__ import annotations

import hashlib

from graph.schema.graph_edges import HasPart
from graph.schema.graph_nodes import ExternalChunk
from graph.schema.node_ids import external_chunk_id


MAX_CHUNK_CHARS = 1200
_LABEL_BY_KIND = {
    "sql": "SQL_STATEMENT",
    "config": "CONFIG_SECTION",
    "artifact": "ARTIFACT_CHUNK",
    "custom": "CHUNK",
}


class ExternalChunkIntegrityError(ValueError):
    """Raised when an anchor/part sequence is incomplete or altered."""


def reconstruct_external_chunk(anchor: ExternalChunk, parts: list[ExternalChunk]) -> str:
    """Reassemble one anchor after validating order, ownership, length and hash."""
    if anchor.part_index != 0 or anchor.anchor_id:
        raise ExternalChunkIntegrityError(f"invalid external anchor: {anchor.id}")
    ordered = sorted(parts, key=lambda part: part.part_index)
    if [part.part_index for part in ordered] != list(range(1, len(ordered) + 1)):
        raise ExternalChunkIntegrityError(f"non-contiguous parts for anchor {anchor.id}")
    if any(part.anchor_id != anchor.id for part in ordered):
        raise ExternalChunkIntegrityError(f"foreign part attached to anchor {anchor.id}")
    text = anchor.text + "".join(part.text for part in ordered)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if any(
        chunk.full_len != len(text) or chunk.content_hash != digest
        for chunk in (anchor, *ordered)
    ):
        raise ExternalChunkIntegrityError(f"content hash/length mismatch for anchor {anchor.id}")
    return text


def split_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split without loss, preferring line boundaries and enforcing ``limit``."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("chunk limit must be a positive integer")
    if not text:
        return [""]
    parts: list[str] = []
    buffer = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if buffer:
                parts.append(buffer)
                buffer = ""
            parts.append(line[:limit])
            line = line[limit:]
        if buffer and len(buffer) + len(line) > limit:
            parts.append(buffer)
            buffer = ""
        buffer += line
    if buffer or not parts:
        parts.append(buffer)
    return parts


def make_external_chunks(
    *,
    file_id: str,
    key: str,
    kind: str,
    text: str,
    file_path: str,
    start_line: int,
    end_line: int,
    sql_type: str = "",
    related_fqns: tuple[str, ...] = (),
) -> tuple[list[ExternalChunk], list[HasPart]]:
    """Create one key-bearing anchor plus ordered continuation parts."""
    label = _LABEL_BY_KIND.get(kind)
    if label is None:
        raise ValueError(f"unsupported external chunk kind: {kind!r}")
    if not key.strip():
        raise ValueError("external chunk key must be non-empty")
    pieces = split_text(text)
    anchor_id = external_chunk_id(file_id, key, 0)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunks: list[ExternalChunk] = []
    edges: list[HasPart] = []
    for index, piece in enumerate(pieces):
        chunk_id = external_chunk_id(file_id, key, index)
        chunks.append(ExternalChunk(
            id=chunk_id,
            name=key if index == 0 else f"part {index}",
            key=key if index == 0 else "",
            kind=kind,
            label=label,
            text=piece,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            part_index=index,
            anchor_id="" if index == 0 else anchor_id,
            full_len=len(text),
            content_hash=digest,
            sql_type=sql_type,
            related_fqns=related_fqns,
        ))
        if index:
            edges.append(HasPart(source_id=anchor_id, target_id=chunk_id, part_index=index))
    return chunks, edges
