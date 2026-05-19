"""표준 미들웨어 스택 헬퍼 — 모든 Agent / Subagent 가 호출.

**deepagents subagent 미들웨어 미상속 버그 (#2744) 회피**:
모든 Agent / Subagent 가 이 헬퍼를 호출해서 미들웨어 명시 주입.

**원본 ``analyzer/strategy/framework/agent/middleware.py`` 의 ``build_common_middleware`` +
plan §4 M2/M3 명시 자체 미들웨어** 를 모두 묶음:

| 출처 | 미들웨어 |
|---|---|
| 원본 (LangChain 표준) | TodoListMiddleware / ContextEditingMiddleware / SummarizationMiddleware |
| 원본 (deepagents) | PatchToolCallsMiddleware |
| plan + 자체 구현 | TodoTracker / ToolEventEmitter / CostTracking / ModelCallLimit / StripAnthropicCacheControl / StripDynamicSystemPrompt / CacheFriendlyOrder |

**모델 라우팅 정책**: Agent 안에서는 1개 모델만 사용 (LangChain 1.x 제약). 모델별 라우팅이
필요한 단순 LLM 호출은 ``yaml_llm_runner.run_yaml_rule(model_override=...)`` 로 호출별 명시.

**새 미들웨어 추가 = 이 함수 1곳만 수정** → 모든 Agent 자동 흡수.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents.middleware import (
    ClearToolUsesEdit,
    ContextEditingMiddleware,
    SummarizationMiddleware,
    TodoListMiddleware,
)

from shared.llm.cost_budget import CostBudget
from shared.llm.middleware.cache_friendly_order import CacheFriendlyOrderMiddleware
from shared.llm.middleware.call_limit import ModelCallLimitMiddleware
from shared.llm.middleware.cost_tracking import CostTrackingMiddleware
from shared.llm.middleware.strip_cache_control import StripAnthropicCacheControlMiddleware
from shared.llm.middleware.strip_dynamic_system_prompt import StripDynamicSystemPromptMiddleware
from shared.llm.middleware.todo_tracker import TodoTrackerMiddleware
from shared.llm.middleware.tool_event_emitter import ToolEventEmitterMiddleware
from shared.llm.token_usage import LatencyBasedCacheEstimator


# 원본 운영 임계값 (analyzer/strategy/framework/agent/middleware.py 와 동일).
# 256K context 모델 (Gemma-4-31b-it / Qwen3-Next) 기준.
_TOOL_RESULT_CLEAR_TRIGGER_TOKENS = 128_000
_TOOL_RESULT_KEEP_LAST = 3
_SUMMARIZE_TRIGGER_TOKENS = 200_000


def build_standard_stack(
    agent_name: str,
    max_calls: int,
    run_dir: Path,
    cost_budget: CostBudget,
    llm_model: Any,
    stream_emit: Optional[Callable[[dict], None]] = None,
    tool_messages: Optional[dict[str, str]] = None,
    cache_estimator: Optional[LatencyBasedCacheEstimator] = None,
) -> list:
    """모든 Agent / Subagent 가 사용할 표준 미들웨어 스택.

    Args:
        agent_name: ``"profile"`` / ``"linking_subagent"`` 등 — 로그 키 + 스트림 이벤트 키.
        max_calls: 이 Agent 의 LLM 호출 한도.
        run_dir: ``logs/analysis.run/latest/`` 경로.
        cost_budget: run 1회의 ``CostBudget``.
        llm_model: ``SummarizationMiddleware`` 가 컨텍스트 요약에 사용할 LLM.
        stream_emit: 프론트 NDJSON 스트림 emit 함수.
        tool_messages: 도구 이름 → 친화 메시지 template.
        cache_estimator: ``cached_tokens`` 안 보이는 환경의 fallback.

    Returns:
        미들웨어 인스턴스 list — Agent 정의에 그대로 전달.
    """
    return [
        # 원본 LangChain 표준 ────────────────────────────────
        # write_todos 도구 자동 제공 + state.todos 누적
        TodoListMiddleware(),
        # 도구 결과 누적 시 정리 (trigger=128K, keep=3)
        ContextEditingMiddleware(edits=[
            ClearToolUsesEdit(
                trigger=_TOOL_RESULT_CLEAR_TRIGGER_TOKENS,
                keep=_TOOL_RESULT_KEEP_LAST,
            ),
        ]),
        # 컨텍스트 임계 초과 시 자동 요약
        SummarizationMiddleware(
            model=llm_model,
            trigger=("tokens", _SUMMARIZE_TRIGGER_TOKENS),
        ),
        # deepagents 도구 호출 패치 (LangChain create_agent 와 도구 호출 호환)
        PatchToolCallsMiddleware(),

        # plan + 자체 구현 ──────────────────────────────────
        # Todo / Tool event — 프론트 NDJSON 스트림 push (부수: jsonl 적재)
        TodoTrackerMiddleware(
            agent_name=agent_name,
            log_path=run_dir / "todos.jsonl",
            stream_emit=stream_emit,
        ),
        ToolEventEmitterMiddleware(
            agent_name=agent_name,
            log_path=run_dir / "tool_events.jsonl",
            stream_emit=stream_emit,
            tool_messages=tool_messages,
        ),
        # 비용 측정 + 회로 차단기 ($400 사고 재발 방지)
        CostTrackingMiddleware(
            usage_log_path=run_dir / "usage.jsonl",
            cost_budget=cost_budget,
            agent_name=agent_name,
            cache_estimator=cache_estimator,
        ),
        # 폭주 차단
        ModelCallLimitMiddleware(
            agent_name=agent_name,
            max_calls=max_calls,
        ),
        # 캐시 친화 (provider 누수 / system 동적값 / volatile 정렬)
        StripAnthropicCacheControlMiddleware(),
        StripDynamicSystemPromptMiddleware(),
        CacheFriendlyOrderMiddleware(),
    ]
