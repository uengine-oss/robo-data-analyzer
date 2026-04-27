"""LangChain Agent 공용 미들웨어.

scanning / linking 양쪽 에이전트가 동일한 미들웨어 세트를 쓴다.

  1. **TodoListMiddleware** — 다단계 분석 진행 추적
  2. **ContextEditingMiddleware** — 도구 결과 누적 시 정리 (keep 마지막 3건)
  3. **SummarizationMiddleware** — 컨텍스트 임계 초과 시 자동 요약
  4. **PatchToolCallsMiddleware** — deepagents 도구 호출 패치
"""

from __future__ import annotations

import logging
from typing import Any, List

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
)

logger = logging.getLogger(__name__)


# 컨텍스트 보호 임계치 — 256K context 모델 (Gemma-4-31b-it / Qwen3-Next) 기준.
# 도구 결과 누적이 너무 빨리 잘리면 에이전트가 같은 chunk 재호출하는 현상 관측 →
# trigger 를 native context 의 50% 지점으로 완화 (clear=128K, keep 5건).
# Summarize 는 그보다 위 (200K) 에서 발동 — 사실상 거의 안 걸리게.
TOOL_RESULT_CLEAR_TRIGGER_TOKENS = 128_000
TOOL_RESULT_KEEP_LAST = 3
SUMMARIZE_TRIGGER_TOKENS = 200_000


def build_common_middleware(model: Any) -> List[Any]:
    """scanning / linking 공용 미들웨어 세트."""
    return [
        TodoListMiddleware(),
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(
                trigger=TOOL_RESULT_CLEAR_TRIGGER_TOKENS,
                keep=TOOL_RESULT_KEEP_LAST,
            ),
        ]),
        SummarizationMiddleware(
            model=model,
            trigger=("tokens", SUMMARIZE_TRIGGER_TOKENS),
        ),
        PatchToolCallsMiddleware(),
    ]
