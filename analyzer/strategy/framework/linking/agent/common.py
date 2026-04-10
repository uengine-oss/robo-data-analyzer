"""DeepAgent 공통 초기화.

DeepAgent 실행에 필요한 초기화 로직:
  1. deepagents 라이브러리 import 검증
  2. LLM 모델 인스턴스 생성
  3. ChunkBundle 인덱스 보장 (state에 없으면 빌드)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ChunkBundle 인덱스 보장
# ---------------------------------------------------------------------------


def _ensure_bundle_indexes(state: Dict[str, Any]) -> None:
    """state에 span_index / chunk_index가 없으면 ChunkBundle로부터 빌드한다.

    이미 존재하면 아무것도 하지 않는다.
    빌드 결과는 state에 직접 저장하여 이후 호출에서 재사용한다.
    """
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
    logger.info(
        "[DEEP_AGENT] ChunkBundle 인덱스 빌드 완료 | span=%d chunk=%d",
        len(span_index), len(chunk_index),
    )


# ---------------------------------------------------------------------------
# DeepAgent 환경 초기화
# ---------------------------------------------------------------------------


def _init_deep_agent_env(
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[Any, Any]:
    """DeepAgent 실행 환경을 초기화한다.

    수행 작업:
      1. deepagents 라이브러리 import (미설치 시 ImportError)
      2. run_dir을 state에 설정
      3. ChunkBundle 인덱스 보장
      4. LLM 모델 인스턴스 생성

    Args:
        state: pipeline state. span_index/chunk_index가 없으면 자동 빌드.
        api_key: LLM API 키 (없으면 기본값 사용).
        run_dir: 실행 디렉토리.

    Returns:
        (create_deep_agent, model) — 에이전트 생성 함수와 LLM 모델.

    Raises:
        ImportError: deepagents 미설치 시.
    """
    from deepagents import create_deep_agent
    from client.llm_client import get_llm

    if run_dir:
        state.setdefault("run_dir", str(run_dir))

    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()
    return create_deep_agent, model
