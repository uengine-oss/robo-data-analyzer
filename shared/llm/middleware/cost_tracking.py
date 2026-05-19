"""CostTrackingMiddleware — 모든 Agent LLM 호출 토큰/비용 자동 측정 + 회로 차단.

**$400 사고 재발 방지의 핵심 인프라**:
- 모든 LLM 호출 (Profile / Linking Agent 내부 포함) 이 이 미들웨어 통과.
- ``usage.jsonl`` 적재 (1 호출 = 1 줄) — ``usage_jsonl_writer.append_usage_record`` 공유.
- ``CostBudget.add_sync()`` 호출 — 임계 초과 시 ``BudgetExceededError`` raise.

**deepagents subagent 미들웨어 미상속 버그 (#2744) 회피**:
subagent 정의 시 이 미들웨어를 명시 주입 (``standard_stack`` 헬퍼).

**단순 LLM 호출** (Agent 외부, ``yaml_llm_runner.run_yaml_rule``) 도 같은
``append_usage_record`` 헬퍼 호출 → usage.jsonl 형식 통일.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware

from shared.llm.cost_budget import CostBudget
from shared.llm.llm_pricing import compute_cost
from shared.llm.token_usage import LatencyBasedCacheEstimator, track_usage
from shared.llm.usage_jsonl_writer import append_usage_record
from shared.logging_and_stream.logger import log_info, log_warning
from shared.logging_and_stream.prompt_logger import append_agent_turn


class CostTrackingMiddleware(AgentMiddleware):
    """Agent LLM 호출 토큰/비용 자동 측정 + usage.jsonl 적재 + 비용 회로 차단기."""

    def __init__(
        self,
        usage_log_path: Path,
        cost_budget: CostBudget,
        agent_name: str = "default",
        cache_estimator: Optional[LatencyBasedCacheEstimator] = None,
    ):
        super().__init__()
        self.usage_log_path = Path(usage_log_path)
        self.cost_budget = cost_budget
        self.agent_name = agent_name
        self.cache_estimator = cache_estimator
        # before_model → after_model 페어는 같은 Agent 인스턴스 안에서 직렬 호출됨
        # (LangChain 1.x AgentMiddleware 보장). state id 기반은 매 hook 사이에
        # state 가 mutate copy 되어 회수 실패 → 단일 instance 변수로 변경.
        self._last_call_start: Optional[float] = None
        # turn 별 본문 적재 — 직전 turn 까지의 messages 길이. 새 turn 의 user 메시지만 추출.
        # 같은 middleware 인스턴스가 한 invoke 동안 재사용 → 0 으로 초기화.
        self._last_msg_count: int = 0

    def before_model(self, state: Any, runtime: Any) -> Any:
        """LLM 호출 직전 — 시작 시각 기록 + analyzer.log 진행 라인.

        호출 시작 시점 라인이 없으면 LLM 응답 대기 vs 행 / 데드 구분 불가 →
        ``▶ 시작`` 라인 1 줄 명시. ``after_model`` 의 ``✓ 완료`` 와 페어.
        """
        self._last_call_start = time.monotonic()
        log_info("llm.call", "start", agent=self.agent_name)
        return state

    def after_model(self, state: Any, runtime: Any) -> Any:
        """LLM 호출 직후 — 토큰 추출 → usage.jsonl 적재 → 비용 차단기."""
        start_ts = self._last_call_start if self._last_call_start is not None else time.monotonic()
        self._last_call_start = None
        latency_ms = int((time.monotonic() - start_ts) * 1000)

        messages = state.get("messages") if isinstance(state, dict) else []
        if not messages:
            log_warning(
                "shared.llm.middleware.cost_tracking",
                "after_model",
                "state.messages 비어있음 — 토큰 측정 건너뜀 (측정 사각지대)",
                agent=self.agent_name,
            )
            return state

        last_msg = messages[-1]
        if not hasattr(last_msg, "usage_metadata"):
            # 도구 결과 메시지 등 — LLM 응답 아니므로 측정 X. 경고 X (정상).
            return state

        # 모델 이름 추출 (response_metadata 또는 fallback)
        response_meta = getattr(last_msg, "response_metadata", {}) or {}
        model = response_meta.get("model_name") or response_meta.get("model")
        if not model:
            model = "unknown"
            log_warning(
                "shared.llm.middleware.cost_tracking",
                "after_model",
                "response_metadata 에서 model 이름 추출 실패 — 'unknown' 으로 적재됨",
                agent=self.agent_name,
            )

        record = track_usage(
            last_msg,
            model=model,
            latency_ms=latency_ms,
            cache_estimator=self.cache_estimator,
        )
        cost = compute_cost(record)

        append_usage_record(
            usage_log_path=self.usage_log_path,
            agent_name=self.agent_name,
            record=record,
            cost_usd=cost,
        )

        # tail -f analyzer.log 진행 추적용 — 한 LLM 호출 = 한 줄.
        log_info(
            "llm.call",
            "done",
            agent=self.agent_name,
            model=model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_tokens=record.cached_tokens or None,
            cost_usd=f"{cost:.6f}",
            elapsed_ms=latency_ms,
        )

        # ★ Agent reasoning + 도구 결정 사후 분석 — turn 본문 적재.
        # ``begin_agent_invoke`` 가 호출된 컨텍스트면 turn 누적, 아니면 silent skip.
        # 한 invoke 의 흐름이 한 파일에 (``agent_<name>.json`` 또는 case 별).
        # 실패는 graceful (분석 자체 막지 않음).
        try:
            self._append_turn(
                messages=messages,
                model=model,
                latency_ms=latency_ms,
                cost_usd=cost,
                record=record,
            )
        except Exception as e:  # noqa: BLE001 — 로깅 실패는 분석 막지 않음
            log_warning(
                "shared.llm.middleware.cost_tracking",
                "append_turn",
                "Agent turn 적재 실패 — 분석은 계속",
                agent=self.agent_name,
                error=f"{type(e).__name__}: {e}",
            )

        # 비용 차단기 — 임계 초과 시 BudgetExceededError raise (silent 처리 X).
        self.cost_budget.add_sync(cost)
        return state

    def _append_turn(
        self,
        messages: list,
        model: str,
        latency_ms: int,
        cost_usd: float,
        record: Any,
    ) -> None:
        """직전 turn 이후 새로 추가된 message 만 user_content 로 직렬화 + turn append.

        - 첫 turn: ``messages = [System, Human, AIMessage]`` → user = [Human], response = AIMessage.
        - 두 번째 turn: ``messages = [..., ToolMessage(s), AIMessage]`` → user = [ToolMessage(s)], response = AIMessage.
        """
        if not messages:
            return

        # 새 message 만 슬라이스 (직전 turn 이후 추가분 ~ 마지막 assistant 직전까지)
        start_idx = self._last_msg_count if self._last_msg_count > 0 else 1
        new_msgs = messages[start_idx:-1]            # system 첫 turn 자동 제외 ([1:-1])
        response_msg = messages[-1]

        user_content = "\n\n".join(
            f"[{getattr(m, 'type', m.__class__.__name__)}]\n{_msg_content_str(m)}"
            for m in new_msgs
        )

        response = {
            "type": getattr(response_msg, "type", response_msg.__class__.__name__),
            "content": _msg_content_str(response_msg),
            "tool_calls": getattr(response_msg, "tool_calls", None) or [],
        }

        metadata = {
            "model": model,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cached_tokens": record.cached_tokens,
            "latency_ms": latency_ms,
            "cost_usd": cost_usd,
        }

        append_agent_turn(user_content=user_content, response=response, metadata=metadata)
        self._last_msg_count = len(messages)


def _msg_content_str(msg: Any) -> str:
    """LangChain message 의 content → 문자열 (list 형식이면 text 부분 join)."""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return str(content)
