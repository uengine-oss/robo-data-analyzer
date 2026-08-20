"""표준 미들웨어 스택 헬퍼 — 모든 Agent / Subagent 가 호출.

**deepagents subagent 미들웨어 미상속 버그 (#2744) 회피**:
모든 Agent / Subagent 가 이 헬퍼를 호출해서 미들웨어 명시 주입.

**원본 ``analyzer/strategy/framework/agent/middleware.py`` 의 ``build_common_middleware`` +
plan §4 M2/M3 명시 자체 미들웨어** 를 모두 묶음:

| 출처 | 미들웨어 |
|---|---|
| 원본 (LangChain 표준) | TodoListMiddleware |
| 원본 (deepagents) | PatchToolCallsMiddleware |
| plan + 자체 구현 | TodoTracker / ToolEventEmitter / CostTracking / ModelCallLimit / StripAnthropicCacheControl / StripDynamicSystemPrompt / CacheFriendlyOrder |

**모델 라우팅 정책**: Agent 안에서는 1개 모델만 사용 (LangChain 1.x 제약). 모델별 라우팅이
필요한 단순 LLM 호출은 ``yaml_llm_runner.run_yaml_rule(model_override=...)`` 로 호출별 명시.

**새 미들웨어 추가 = 이 함수 1곳만 수정** → 모든 Agent 자동 흡수.

---

**왜 `deepagents` 하네스를 쓰지 않고 `create_agent` 위에 직접 조립하는가** (2026-08-08 조사,
사용자 확정 — 같은 질문을 다시 조사하지 말 것)

LangChain 공식 문서는 둘을 **같은 기반 위의 대안**으로 규정한다: *"`deepagents` is a
standalone library built on top of LangChain's core building blocks for agents."* /
*"For building custom agents **without these built-in capabilities**, consider using
LangChain's `create_agent`."* (통설인 "`create_agent` 는 deepagents 의 경량판"도, 커뮤니티의
"deepagents 는 `create_agent` 위에 얹혔다"도 공식 서술과 다르다.)

`deepagents` 는 **도구 9개가 기본 활성**이다 — 파일시스템 8종(`ls`·`read_file`·`write_file`·
`edit_file`·`delete`·`glob`·`grep`·`execute`) + 서브에이전트 `task`. (태스크 플래닝은
v0.7부터 opt-in.) 우리 에이전트는 그중 무엇도 쓰지 않는다 — `agents/scan.py` 는 "탐색 도구 +
정규식 검증 도구를 든 **단일** 에이전트"다. 쓰지 않는 도구 정의가 매 호출 프롬프트에 실리면
KV 공유 접두사와 입력 토큰만 늘어난다(헌법 V · 입력 노이즈 최소화).

**따라서 하네스 선택으로 인한 성능 손해는 없다.** deepagents 가 유리한 것은 파일시스템·위임·
장기 컨텍스트가 실제로 필요한 작업이며, 이 저장소의 에이전트는 그 범주가 아니다.

**⚠ 남은 열린 항목** — 위 표의 `PatchToolCallsMiddleware` **한 클래스** 때문에
`deepagents>=0.2.0` 전체를 의존하며, 그것이 LangChain 1.x 를 끌어올리는 주체다
(`requirements.txt:5,14`). "필요한 것만 넣는다"는 설계 의도와 어긋나는 잔재일 수 있다.
**걷어내려면 이 미들웨어가 무엇을 고쳐 주는지부터 확정해야 한다** — 확인 없이 제거하면
`create_agent` 와의 도구 호출 호환이 깨진다(아래 주입 지점 주석 참조).

출처: https://docs.langchain.com/oss/python/deepagents/overview ·
https://github.com/langchain-ai/deepagents
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from langchain.agents.middleware import TodoListMiddleware

from llm.usage.cost_budget import CostBudget
from agents.middleware.cache_friendly_order import CacheFriendlyOrderMiddleware
from agents.middleware.call_limit import ModelCallLimitMiddleware
from agents.middleware.cost_tracking import CostTrackingMiddleware
from agents.middleware.strip_cache_control import StripAnthropicCacheControlMiddleware
from agents.middleware.strip_dynamic_prompt import StripDynamicSystemPromptMiddleware
from agents.middleware.todos import TodoTrackerMiddleware
from agents.middleware.tool_events import ToolEventEmitterMiddleware
from llm.usage.token_usage import LatencyBasedCacheEstimator


def build_standard_stack(
    agent_name: str,
    max_calls: int,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
    tool_messages: Optional[dict[str, str]] = None,
    cache_estimator: Optional[LatencyBasedCacheEstimator] = None,
) -> list:
    """모든 Agent / Subagent 가 사용할 표준 미들웨어 스택.

    Args:
        agent_name: ``"scan"`` / ``"linking_subagent"`` 등 — 로그 키 + 스트림 이벤트 키.
        max_calls: 이 Agent 의 LLM 호출 한도.
        run_dir: run 로그 디렉토리 (``logs/``).
        cost_budget: run 1회의 ``CostBudget``.
        stream_emit: 프론트 NDJSON 스트림 emit 함수.
        tool_messages: 도구 이름 → 친화 메시지 template.
        cache_estimator: ``cached_tokens`` 안 보이는 환경의 fallback.

    Returns:
        미들웨어 인스턴스 list — Agent 정의에 그대로 전달.
    """
    return [
        # LangChain 표준 ─────────────────────────────────────
        # write_todos 도구 자동 제공 + state.todos 누적
        TodoListMiddleware(),
        # 탐색 transcript와 tool evidence는 원문 그대로 보존한다. 컨텍스트 초과는
        # 명시적 실패로 드러내며, 과거 evidence 삭제/LLM 요약으로 성공처럼 바꾸지 않는다.
        # 큰 탐색의 무손실 분할·exact-once closure는 spec 131 C-017/C-018 책임이다.
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
