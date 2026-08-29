"""표준 미들웨어 스택 헬퍼 — 모든 Analyzer Agent가 호출.

도구 이벤트, 비용 계측, provider별 동적 메타데이터 제거, 캐시 친화 순서를 한곳에서
조립한다. 각 Agent는 LangChain ``create_agent``에 필요한 도구와 이 스택을 직접 넘긴다.
현재 제품 경로는 과거 AI/tool 대화 상태를 재개하거나 subagent에 위임하지 않는다.

**모델 라우팅 정책**: Agent 안에서는 1개 모델만 사용 (LangChain 1.x 제약). 모델별 라우팅이
필요한 단순 LLM 호출은 ``yaml_llm_runner.run_yaml_rule(model_override=...)`` 로 호출별 명시.

**새 미들웨어 추가 = 이 함수 1곳만 수정** → 모든 Agent 자동 흡수.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from llm.usage.cost_budget import CostBudget
from agents.middleware.cache_friendly_order import CacheFriendlyOrderMiddleware
from agents.middleware.cost_tracking import CostTrackingMiddleware
from agents.middleware.strip_cache_control import StripAnthropicCacheControlMiddleware
from agents.middleware.strip_dynamic_prompt import StripDynamicSystemPromptMiddleware
from agents.middleware.tool_events import ToolEventEmitterMiddleware


def build_standard_stack(
    agent_name: str,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
    tool_messages: Optional[dict[str, str]] = None,
) -> list:
    """모든 Analyzer Agent가 사용할 표준 미들웨어 스택.

    Args:
        agent_name: ``"scan"`` / ``"linking_subagent"`` 등 — 로그 키 + 스트림 이벤트 키.
        run_dir: run 로그 디렉토리 (``logs/``).
        cost_budget: run 1회의 ``CostBudget``.
        stream_emit: 프론트 NDJSON 스트림 emit 함수.
        tool_messages: 도구 이름 → 친화 메시지 template.

    Returns:
        미들웨어 인스턴스 list — Agent 정의에 그대로 전달.
    """
    return [
        # 탐색 transcript와 tool evidence는 원문 그대로 보존한다. 컨텍스트 초과는
        # 명시적 실패로 드러내며, 과거 evidence 삭제/LLM 요약으로 성공처럼 바꾸지 않는다.
        # 큰 탐색의 무손실 분할·exact-once closure는 spec 131 C-017/C-018 책임이다.
        # Tool event — 프론트 NDJSON 스트림 push (부수: jsonl 적재)
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
        ),
        # 캐시 친화 (provider 누수 / system 동적값 / volatile 정렬)
        StripAnthropicCacheControlMiddleware(),
        StripDynamicSystemPromptMiddleware(),
        CacheFriendlyOrderMiddleware(),
    ]
