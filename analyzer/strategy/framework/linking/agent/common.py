"""DeepAgent 공통 초기화 및 ChunkBundle 인덱스 보장."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _ensure_bundle_indexes(state: Dict[str, Any]) -> None:
    """state 에 span_index / chunk_index 가 없으면 ChunkBundle 로부터 빌드하여 채운다."""
    if state.get("span_index") and state.get("chunk_index"):
        return
    from analyzer.strategy.framework.parsing.chunk_bundle import (
        build_bundle_span_index,
        _get_chunk_bundle_dir_from_run_dir,
    )
    run_dir = state.get("run_dir")
    data_dir = state.get("data_dir") or ""
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir) if run_dir else None
    span_index, chunk_index = build_bundle_span_index(
        data_dir,
        chunk_bundle_dir=chunk_dir,
        artifact_index=state.get("artifact_index"),
        sql_index=state.get("sql_index"),
    )
    state["span_index"] = span_index
    state["chunk_index"] = chunk_index
    logger.info("[PHASE:2:DEEP_AGENT] bundle index 빌드 완료 | span=%d chunk=%d", len(span_index), len(chunk_index))


def _init_deep_agent_env(
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[Any, Any]:
    """DeepAgent 공통 초기화: import 검증, state 설정, 모델 생성."""
    from deepagents import create_deep_agent
    from client.llm_client import get_llm

    if run_dir:
        state.setdefault("run_dir", str(run_dir))
    _ensure_bundle_indexes(state)
    model = get_llm(api_key=api_key) if api_key else get_llm()
    return create_deep_agent, model
