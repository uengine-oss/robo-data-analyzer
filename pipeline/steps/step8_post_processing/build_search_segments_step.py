"""Convert each publishable routine summary into one complete SearchSegment.

This step reads validated in-memory meanings once and performs no graph access.
Product nodes, relationships, and indexes belong to the final publication path.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from llm import embedder
from pipeline.execution.step_config import step_config
from pipeline.languages import ROUTINE_LABELS
from pipeline.publication.search_segments import search_segment_stable_id
from search.corpus.embed_material import compose_embed_text
from shared.config.settings import SETTINGS
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info


_STEP = "step8.build_search_segments"


async def build_search_segments_step(
    state: dict,
    config: Optional[RunnableConfig] = None,
) -> dict:
    """Return the entire DB-independent SearchSegment population."""
    if not SETTINGS.analysis_pipeline.embed_enabled:
        log_info(_STEP, "skip", reason="embedding explicitly disabled")
        return {"search_segments": []}

    cfg = step_config(config)
    stream = cfg.get("stream")
    code_nodes = state.get("code_nodes", [])
    started = time.monotonic()
    with step_processing_scope(_STEP, stream):
        segments = await asyncio.to_thread(
            _build_search_segments,
            code_nodes,
            embedder.fingerprint(),
        )
    log_info(
        _STEP,
        "done",
        search_segments=len(segments),
        embedding_model_id=embedder.fingerprint(),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"search_segments": segments}


def _build_search_segments(code_nodes: list, embedding_model_id: str) -> list[dict]:
    """Create one full, source-addressed segment per routine with a summary."""
    material: list[tuple[object, str]] = []
    seen_ids: set[str] = set()
    for node in sorted(code_nodes, key=lambda item: item.id):
        if node.id in seen_ids:
            raise ValueError(f"duplicate CodeNode ID in search population: {node.id!r}")
        seen_ids.add(node.id)
        if node.label not in ROUTINE_LABELS:
            continue
        text = compose_embed_text({"summary": node.summary})
        if not text:
            continue
        material.append((node, text))

    if not material:
        return []
    vectors = embedder.encode_texts([text for _node, text in material])
    if len(vectors) != len(material):
        raise ValueError(
            "embedding provider returned a different population than requested: "
            f"requested={len(material)}, returned={len(vectors)}"
        )

    segments: list[dict] = []
    for (node, text), vector in zip(material, vectors, strict=True):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        stable_id = search_segment_stable_id(
            "CodeNode", node.id, 1, text_hash,
        )
        segment = {
            "stable_id": stable_id,
            "parent_product_type": "CodeNode",
            "parent_stable_id": node.id,
            "ordinal": 1,
            "text_sha256": text_hash,
            "text": text,
            "embedding_model_id": embedding_model_id,
            "embedding": [float(value) for value in vector],
        }
        if (
            isinstance(node.start_line, int)
            and not isinstance(node.start_line, bool)
            and isinstance(node.end_line, int)
            and not isinstance(node.end_line, bool)
            and node.start_line > 0
            and node.end_line >= node.start_line
        ):
            segment["source_start_line"] = node.start_line
            segment["source_end_line"] = node.end_line
        segments.append(segment)
    return segments
