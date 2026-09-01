"""CostTrackingMiddleware — 모든 Agent LLM 호출 측정 + 적재 + 회로 차단 + silent 차단.

**$400 사고 재발 방지의 핵심 인프라**:

- 모든 LLM 호출 (Scan / Linking Agent 내부 포함) 이 이 미들웨어 통과.
- ``usage.jsonl`` 적재 (1 호출 = 1 줄) — ``append_usage_record`` 공유.
- ``CostBudget.add_sync()`` 호출 — 임계 초과 시 ``BudgetExceededError`` raise.
- **silent 차단**: LLM 예외 (401 / 429 / 400 / timeout / network) 모두 명시 로그 + raise.

**단일 hook 통합** (``awrap_model_call``):

LangChain ``before_model`` / ``after_model`` 분업 X — wrap 하나로 5 책임 통일:

1. 시작 시각 + ``▶ 시작`` 라인 (analyzer.log)
2. ``handler(request)`` 호출 — 실제 LLM 호출
3. 정상: ``ModelResponse.result`` 의 AIMessage 추출 → 토큰 → ``usage.jsonl`` + ``✓ 완료``
4. 예외: 명시 로그 (silent 차단) + raise
5. ``CostBudget`` 회로 차단기

**단순 LLM 호출** (Agent 외부, ``yaml_llm_runner.run_yaml_rule``) 도 같은
``append_usage_record`` 헬퍼 호출 → usage.jsonl 형식 통일.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from llm.usage.cost_budget import CostBudget
from llm.prompt_messages import content_to_text
from llm.usage.model_pricing import compute_cost
from llm.usage.token_usage import UnmeteredLlmCallError, track_usage
from llm.usage.usage_jsonl_writer import persist_usage_and_charge_budget
from shared.events.runtime_logger import log_info, log_warning
from llm.request_tracing import append_conversation_turn


class CostTrackingMiddleware(AgentMiddleware):
    """Agent LLM 호출 측정 + 적재 + 회로 차단 + silent 차단 (단일 hook ``awrap_model_call``)."""

    def __init__(
        self,
        usage_log_path: Path,
        cost_budget: CostBudget,
        agent_name: str = "default",
    ):
        super().__init__()
        self.usage_log_path = Path(usage_log_path)
        self.cost_budget = cost_budget
        self.agent_name = agent_name
        # turn 별 본문 적재 — 직전 turn 까지의 messages 길이. 새 turn 의 user 메시지만 슬라이스.
        # 같은 middleware 인스턴스가 한 invoke 동안 재사용 → 0 으로 초기화.
        self._last_msg_count: int = 0

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """LLM 호출 전체 wrap — 시작 / handler / 측정 / 예외 / 적재 / 차단기 통합.

        Raises:
            BudgetExceededError: 비용 임계 초과 시.
            Any LLM exception: silent 차단 — 명시 로그 후 그대로 re-raise.
        """
        wrap_start = time.monotonic()
        log_info("llm.call", "start", agent=self.agent_name)

        try:
            response: ModelResponse = await handler(request)
        except Exception as e:  # noqa: BLE001 — silent 차단 목적, 모든 예외 catch + log + raise
            elapsed_ms = int((time.monotonic() - wrap_start) * 1000)
            log_warning(
                "agents.middleware.cost_tracking",
                "model_error",
                f"LLM 호출 실패 (silent 차단) — {type(e).__name__}: {e}",
                agent=self.agent_name,
                elapsed_ms=elapsed_ms,
            )
            raise

        elapsed_ms = int((time.monotonic() - wrap_start) * 1000)

        # response.result 에서 AIMessage 추출 (usage_metadata 보유).
        # 보통 AIMessage 1개. structured output 시 ToolMessage 동반 가능.
        ai_msg = next(
            (m for m in reversed(response.result) if isinstance(m, AIMessage)),
            None,
        )
        if ai_msg is None:
            log_warning(
                "agents.middleware.cost_tracking",
                "no_ai_message",
                "ModelResponse.result 에 AIMessage 없음 — 계측 불가능 응답을 거부",
                agent=self.agent_name,
            )
            raise UnmeteredLlmCallError(
                f"agent '{self.agent_name}' response has no AIMessage; token usage cannot be measured"
            )

        # 모델 이름 추출
        response_meta = getattr(ai_msg, "response_metadata", {}) or {}
        model = response_meta.get("model_name") or response_meta.get("model")
        if not model:
            model = "unknown"
            log_warning(
                "agents.middleware.cost_tracking",
                "no_model_name",
                "response_metadata 에서 model 이름 추출 실패 — 'unknown' 으로 적재",
                agent=self.agent_name,
            )

        record = track_usage(
            ai_msg,
            model=model,
            latency_ms=elapsed_ms,
        )
        cost = compute_cost(record)

        persist_usage_and_charge_budget(
            usage_log_path=self.usage_log_path,
            agent_name=self.agent_name,
            record=record,
            cost_usd=cost,
            cost_budget=self.cost_budget,
        )

        log_info(
            "llm.call",
            "done",
            agent=self.agent_name,
            model=model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_tokens=record.cached_tokens or None,
            cost_usd=f"{cost:.6f}",
            elapsed_ms=elapsed_ms,
        )

        # Agent reasoning + 도구 결정 사후 분석 — turn 본문 적재.
        # 실패는 graceful (log_warning 명시, silent X — 분석은 계속).
        try:
            self._append_turn(
                request_messages=_extract_state_messages(request),
                response_msg=ai_msg,
                model=model,
                latency_ms=elapsed_ms,
                cost_usd=cost,
                record=record,
            )
        except Exception as e:  # noqa: BLE001 — 로깅 실패는 분석 막지 않음
            log_warning(
                "agents.middleware.cost_tracking",
                "append_turn",
                "Agent turn 적재 실패 — 분석은 계속",
                agent=self.agent_name,
                error=f"{type(e).__name__}: {e}",
            )

        return response

    def _append_turn(
        self, request_messages: list, response_msg: AIMessage, model: str,
        latency_ms: int, cost_usd: float, record: Any,
    ) -> None:
        self._last_msg_count = append_conversation_turn(
            request_messages=request_messages, response_message=response_msg,
            last_message_count=self._last_msg_count, model=model,
            latency_ms=latency_ms, cost_usd=cost_usd, usage=record,
        )

def _extract_state_messages(request: ModelRequest) -> list:
    """``ModelRequest.state`` 에서 messages 목록 추출 — TypedDict / dict 어느 쪽이든 안전."""
    state = getattr(request, "state", None)
    if state is None:
        return []
    if isinstance(state, dict):
        return state.get("messages", []) or []
    return list(getattr(state, "messages", []) or [])


def _msg_content_str(msg: Any) -> str:
    """LangChain message 의 content → 문자열 (list 형식이면 text 부분 join)."""
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        return content_to_text(content)
    return content if isinstance(content, str) else str(content)
