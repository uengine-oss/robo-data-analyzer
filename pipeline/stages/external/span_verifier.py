"""Deterministic span verification (spec 078).

The single gate every semantic span must pass before it may become a chunk:
bounds, ordering, no overlap, non-empty slice.  Verified chunks are produced
by slicing the decoded source here — adapters never hand over text.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from pipeline.stages.external.parsers.span_common import SemanticSpan


class SpanVerificationError(ValueError):
    """A proposed span set violates the source-slice invariants."""


@dataclass(frozen=True)
class VerifiedChunk:
    char_start: int
    char_end: int
    text: str
    keys: tuple[str, ...]
    semantic_kind: str
    name: str
    start_line: int
    end_line: int
    content_hash: str
    addressable_keys: tuple[str, ...] = ()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_spans(source: str, spans: list[SemanticSpan]) -> list[VerifiedChunk]:
    """Validate the invariants and slice chunks from the source, in span order."""
    ordered = sorted(spans, key=lambda span: (span.char_start, span.char_end))
    previous_end = 0
    chunks: list[VerifiedChunk] = []
    for span in ordered:
        if not 0 <= span.char_start < span.char_end <= len(source):
            raise SpanVerificationError(
                f"span out of bounds: ({span.char_start}, {span.char_end}) for len {len(source)}")
        if span.char_start < previous_end:
            raise SpanVerificationError(
                f"span overlap at ({span.char_start}, {span.char_end})")
        previous_end = span.char_end
        text = source[span.char_start:span.char_end]
        if not text.strip():
            raise SpanVerificationError("span selects only whitespace")
        start_line = source.count("\n", 0, span.char_start) + 1
        end_line = start_line + text.count("\n") - (1 if text.endswith("\n") else 0)
        chunks.append(VerifiedChunk(
            char_start=span.char_start,
            char_end=span.char_end,
            text=text,
            keys=span.keys,
            semantic_kind=span.semantic_kind,
            name=span.name or (span.keys[0] if span.keys else ""),
            start_line=start_line,
            end_line=max(start_line, end_line),
            content_hash=sha256_text(text),
            addressable_keys=span.addressable_keys,
        ))
    return chunks
