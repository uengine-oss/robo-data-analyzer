"""LangGraph StateGraph 빌더 — Strategy 의 step 리스트로 그래프 구성.

**조건(if) 분기 0개 — 단, 병렬 그룹은 지원**:
- 빌더는 strategy 가 뭔지 모름. ``strategy.steps`` 를 순회하며 노드 추가만.
- steps 항목이 **함수면 단일 노드**(선형), **tuple 이면 병렬 그룹**(동시 실행 후 합류).
  → 조건 분기가 아니라 결정론 fan-out/fan-in. 병렬 그룹 멤버는 서로 독립이어야 함
  (입력 read-only·return state 키 비충돌 — 충돌 시 LangGraph 가 에러로 표면화).
- 어떤 step 활성? → ``strategy.steps`` 리스트에 포함되냐 여부로 결정.
- 같은 step 인데 처리 방식 다름? → step 함수 안에서 ``cfg["strategy"].<implementer>`` 호출 (DI).

**LangGraph 의 책임**:
- 상태 관리: StateGraph 가 ``WorkflowState`` 자동 전파.
- state 전파: ``Checkpointer`` (MemorySaver) 가 run 1회 안의 superstep state 보존.
- 스트림: ``astream(stream_mode="updates")`` 단일 채널 (폴링 없음).

일시정지/재개/중단은 ``PauseController`` (cfg["pause"]) 가 담당 — step 함수가 안전
지점에서 ``gate()`` 로 소비. 같은 세션 안의 soft hold (디스크 재개 아님).

**새 strategy 추가 = ``pipeline/strategies.py`` 의 ``Strategy(...)`` 선언만**.
코드 어디 분기 추가 X.
"""
from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from pipeline.execution.workflow_state import WorkflowState


def build_langgraph_workflow(
    strategy: Any,
    checkpointer: Optional[Any] = None,
) -> Any:
    """strategy.steps 리스트로 StateGraph 빌드 + compile.

    각 step 은 async function — signature::

        async def step(state: dict, config: Optional[RunnableConfig] = None) -> dict

    ★ ``config`` 의 타입 어노테이션은 반드시 ``RunnableConfig`` 또는
    ``Optional[RunnableConfig]`` 이어야 LangGraph 가 runtime config 를 주입함.
    (LangGraph 1.x ``_internal/_runnable.py`` 의 ``KWARGS_CONFIG_KEYS`` 가 인식하는
    annotation 화이트리스트: ``RunnableConfig`` / ``"RunnableConfig"`` /
    ``Optional[RunnableConfig]`` / ``"Optional[RunnableConfig]"``. ``RunnableConfig | None``
    같은 PEP604 syntax 는 인식 못 함 — 인식 실패 시 step 이 config=None 으로 호출되어
    project / neo_client / stream 전부 None → step 이 ``{}`` 만 반환 (silent 빈 결과).)

    state 는 WorkflowState (TypedDict). step 안에서
    ``cfg = step_config(config)`` 로 strategy / run / cost_budget / neo_client / stream 접근.

    if 분기 0개. for 루프만.

    Args:
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``pipeline/strategies.py``).
        checkpointer: LangGraph Checkpointer (MemorySaver 등) — run 1회 안의 state 전파.

    Returns:
        Compiled StateGraph — ``app.astream(initial_state, config=...)`` 로 실행.
    """
    try:
        steps = list(strategy.steps)
    except (AttributeError, TypeError) as error:
        raise TypeError("strategy.steps must be an iterable of workflow steps") from error
    if not steps:
        raise ValueError("workflow must contain at least one step")

    normalized_groups: list[tuple] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(steps):
        group = entry if isinstance(entry, tuple) else (entry,)
        if not group:
            raise ValueError(f"workflow parallel group at index {index} is empty")
        for step_fn in group:
            if not callable(step_fn):
                raise TypeError(f"workflow step at index {index} is not callable: {step_fn!r}")
            node_name = getattr(step_fn, "__name__", "")
            if not node_name:
                raise ValueError(f"workflow step at index {index} has no __name__")
            if node_name in seen_names:
                raise ValueError(f"duplicate step name: {node_name}")
            seen_names.add(node_name)
        normalized_groups.append(group)

    builder = StateGraph(WorkflowState)

    # prev = 직전 단계의 노드 이름들(항상 list). steps 항목이 **함수면 단일 노드**,
    # **tuple 이면 "병렬 그룹"** — 그룹의 노드들은 직전 단계(들)에서 fan-out 되어 동시
    # 실행되고, 다음 단계는 그룹 전체 완료를 기다려 fan-in(superstep 배리어). 조건 if 분기는
    # 여전히 0개 — 결정론 fan-out/fan-in 만(병렬 그룹은 서로 독립·state 키 비충돌이 전제).
    prev: list = [START]
    for group in normalized_groups:
        names: list[str] = []
        for step_fn in group:
            node_name = step_fn.__name__
            builder.add_node(node_name, step_fn)
            for p in prev:
                builder.add_edge(p, node_name)   # 직전 단계(들) → 이 노드
            names.append(node_name)
        prev = names                              # 다음 단계는 이 노드(들) 완료 후 (fan-in)
    for p in prev:
        builder.add_edge(p, END)

    return builder.compile(checkpointer=checkpointer)
