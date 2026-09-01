"""Build one direct search vector for every summarized routine."""
from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from llm import text_embedder
from llm.usage.cost_budget import CostBudget
from pipeline.execution.step_config import require_step_config_value, step_config
from pipeline.source_languages import ROUTINE_LABELS
from search.corpus.embed_material import compose_embed_text
from shared.config.app_settings import SETTINGS
from shared.events.processing_events import step_processing_scope
from shared.events.runtime_logger import log_info


_STEP = "step8.build_search_embeddings"


async def build_search_embeddings_step(
    state: dict,
    config: Optional[RunnableConfig] = None,
) -> dict:
    """Return DB-independent routine ID/vector pairs."""
    if not SETTINGS.analysis_pipeline.embed_enabled:
        log_info(_STEP, "skip", reason="embedding explicitly disabled")
        return {"routine_embeddings": []}

    cfg = step_config(config)
    stream = cfg.get("stream")
    usage_log_path = None
    cost_budget = None
    if SETTINGS.embedding.provider == "openai":
        run = require_step_config_value(cfg, "run", step=_STEP)
        cost_budget = require_step_config_value(cfg, "cost_budget", step=_STEP)
        usage_log_path = run.run_dir / "usage.jsonl"
    started = time.monotonic()
    with step_processing_scope(_STEP, stream):
        embeddings = await asyncio.to_thread(
            _build_search_embeddings,
            state.get("code_nodes", []),
            usage_log_path=usage_log_path,
            cost_budget=cost_budget,
        )
    log_info(
        _STEP,
        "done",
        routine_embeddings=len(embeddings),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"routine_embeddings": embeddings}


def _build_search_embeddings(
    code_nodes: list,
    *,
    usage_log_path: Path | None = None,
    cost_budget: CostBudget | None = None,
) -> list[dict]:
    material: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    for node in sorted(code_nodes, key=lambda item: item.id):
        if node.id in seen_ids:
            raise ValueError(f"duplicate CodeNode ID in search population: {node.id!r}")
        seen_ids.add(node.id)
        if node.label not in ROUTINE_LABELS:
            continue
        text = compose_embed_text({"summary": node.summary})
        if text:
            material.append((node.id, text))
    if not material:
        return []

    texts = [text for _node_id, text in material]
    if usage_log_path is None:
        vectors = text_embedder.encode_texts(texts)
    else:
        vectors = text_embedder.encode_texts(
            texts,
            usage_log_path=usage_log_path,
            cost_budget=cost_budget,
            agent_name="embedding:routines",
        )
    if len(vectors) != len(material):
        raise ValueError(
            "embedding provider returned a different population than requested: "
            f"requested={len(material)}, returned={len(vectors)}"
        )
    return [
        {
            "routine_id": node_id,
            "embedding": [float(value) for value in vector],
        }
        for (node_id, _text), vector in zip(material, vectors, strict=True)
    ]
