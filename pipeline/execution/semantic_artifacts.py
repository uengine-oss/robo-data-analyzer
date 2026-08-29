"""Content-addressed run artifacts for Analyzer-owned semantic detail."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from pipeline.execution.run_output_context import active_run_dir
from pipeline.models.function_meaning import (
    FunctionMeaningState,
    MeaningStatus,
    MissingContextItem,
)
from pipeline.models.semantic_frame import SemanticFrame


_LOCK = threading.Lock()
_ARTIFACT_DIRECTORY = "semantic-artifacts"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _persist_content_addressed(kind: str, value: Any) -> str:
    encoded = _canonical_bytes(value)
    digest = hashlib.sha256(encoded).hexdigest()
    run_dir = active_run_dir()
    if run_dir is None:
        return digest

    directory = run_dir / _ARTIFACT_DIRECTORY / kind
    target = directory / f"{digest}.json"
    with _LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != encoded:
                raise ValueError(f"content-addressed artifact hash collision: {target}")
            return digest
        temporary = Path(f"{target}.tmp")
        temporary.write_bytes(encoded)
        temporary.replace(target)
    return digest


def persist_function_semantic_frame(frame: SemanticFrame) -> str:
    """Persist the complete closed slot ledger outside the product graph."""
    return persist_semantic_frame(frame, entity_kind="function")


def persist_semantic_frame(frame: SemanticFrame, *, entity_kind: str) -> str:
    """Persist one complete semantic-frame ledger under its product family."""
    normalized_kind = str(entity_kind or "").strip().lower().replace("_", "-")
    if normalized_kind not in {"function", "table", "column", "table-usage"}:
        raise ValueError(f"unsupported semantic-frame entity kind: {entity_kind!r}")
    return _persist_content_addressed(
        f"{normalized_kind}-semantic-frames",
        frame.model_dump(mode="json", by_alias=True),
    )


def persist_function_missing_context(state: FunctionMeaningState) -> str:
    """Persist detailed missing context and return the product-facing hash."""
    if not state.missing_context_items:
        raise ValueError("missing-context artifact requires at least one item")
    return persist_product_missing_context(
        entity_kind="function",
        entity_id=state.function_id,
        meaning_status=state.meaning_status,
        items=state.missing_context_items,
    )


def persist_product_missing_context(
    *,
    entity_kind: str,
    entity_id: str,
    meaning_status: MeaningStatus,
    items: tuple[MissingContextItem, ...],
) -> str:
    """Persist one product entity's detailed missing-context list."""
    normalized_kind = str(entity_kind or "").strip().lower().replace("_", "-")
    if normalized_kind not in {"function", "table", "column", "table-usage"}:
        raise ValueError(f"unsupported missing-context entity kind: {entity_kind!r}")
    if not str(entity_id or "").strip():
        raise ValueError("missing-context entity id is blank")
    if meaning_status == "sufficient" or not items:
        raise ValueError("missing-context artifact requires a non-sufficient state")
    return _persist_content_addressed(
        f"{normalized_kind}-missing-context",
        {
            "schema": f"{normalized_kind}-missing-context/v1",
            "entity_id": entity_id,
            "meaning_status": meaning_status,
            "missing_context_items": [
                item.model_dump(mode="json") for item in items
            ],
        },
    )
