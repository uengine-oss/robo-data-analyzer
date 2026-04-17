"""Agent 공통 초기화.

LLM Agent 실행에 필요한 초기화 로직:
  1. LLM 모델 인스턴스 생성
  2. create_agent + 최소 미들웨어로 에이전트 팩토리 구성
  3. ChunkBundle 인덱스 보장 (state에 없으면 빌드)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

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
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir)

    span_index, chunk_index = build_bundle_span_index(
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


def _build_agent_factory(model: Any) -> Callable:
    """create_agent + 최소 미들웨어를 바인딩한 에이전트 팩토리를 반환한다.

    FilesystemMiddleware 제거 → 내장 도구(write_file/edit_file/execute 등) 비활성화.
    MemoryMiddleware / AnthropicPromptCachingMiddleware 제거 (불필요).

    미들웨어 (순서: 가벼운 방어 → 무거운 작업 → 최종 상한):
      1. TodoListMiddleware: 계획 관리
      2. ContextEditingMiddleware + ClearToolUsesEdit: 오래된 tool 결과 pruning (70K trigger)
      3. SummarizationMiddleware: context overflow 안전망 (85K trigger)
      4. ToolCallLimitMiddleware: run당 총 tool 호출 상한
      5. ModelCallLimitMiddleware: LLM 호출 상한 (무한루프·비용 방어)
      6. PatchToolCallsMiddleware: tool call 보정
    """
    from langchain.agents import create_agent
    from langchain.agents.middleware import (
        ContextEditingMiddleware,
        ClearToolUsesEdit,
        ModelCallLimitMiddleware,
        SummarizationMiddleware,
        TodoListMiddleware,
        ToolCallLimitMiddleware,
    )
    from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware

    middleware = [
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(trigger=70_000, keep=3),
        ]),
        SummarizationMiddleware(model=model, trigger=("tokens", 85_000)),
        ToolCallLimitMiddleware(run_limit=50),
        ModelCallLimitMiddleware(run_limit=25, thread_limit=200),
        PatchToolCallsMiddleware(),
    ]

    def factory(*, tools: list, system_prompt: str, **kwargs: Any) -> Any:
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middleware,
            **kwargs,
        )

    return factory


def _init_agent_env(
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[Callable, Any]:
    """Agent 실행 환경을 초기화한다.

    수행 작업:
      1. run_dir을 state에 설정
      2. ChunkBundle 인덱스 보장
      3. LLM 모델 인스턴스 생성
      4. create_agent 기반 팩토리 구성 (최소 미들웨어)

    Args:
        state: pipeline state. span_index/chunk_index가 없으면 자동 빌드.
        api_key: LLM API 키 (없으면 기본값 사용).
        run_dir: 실행 디렉토리.

    Returns:
        (agent_factory, model) — 에이전트 생성 팩토리와 LLM 모델.
    """
    from client.llm_client import get_llm

    if run_dir:
        state.setdefault("run_dir", str(run_dir))

    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()
    return _build_agent_factory(model), model
