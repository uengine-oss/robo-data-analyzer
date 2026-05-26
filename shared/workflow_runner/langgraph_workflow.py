"""LangGraph StateGraph 빌더 — Strategy 의 step 리스트로 그래프 구성.

**분기 0개**:
- 빌더는 strategy 가 뭔지 모름. ``for step_fn in strategy.steps`` 로 노드 추가만.
- 어떤 step 활성? → ``strategy.steps`` 리스트에 포함되냐 여부로 결정.
- 같은 step 인데 처리 방식 다름? → step 함수 안에서 ``cfg["strategy"].<implementer>`` 호출 (DI).

**LangGraph 의 책임 — 별도 컨트롤러 X**:
- 상태 관리: StateGraph 가 ``WorkflowState`` 자동 전파.
- state 전파: ``Checkpointer`` (MemorySaver) 가 run 1회 안의 superstep state 보존.
  (resume 는 현재 미지원 — 매 ``/analyze`` = 독립 run.)
- 스트림: ``astream(stream_mode="updates")`` 단일 채널 (폴링 없음).

**새 strategy 추가 = ``analysis_pipelines.py`` 의 ``Strategy(...)`` 선언만**.
코드 어디 분기 추가 X.
"""
from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from shared.workflow_runner.workflow_state import WorkflowState


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
        strategy: ``FRAMEWORK_PIPELINE`` 또는 ``DBMS_PIPELINE`` (``analysis_pipelines.py``).
        checkpointer: LangGraph Checkpointer (MemorySaver 등) — run 1회 안의 state 전파.

    Returns:
        Compiled StateGraph — ``app.astream(initial_state, config=...)`` 로 실행.
    """
    builder = StateGraph(WorkflowState)

    prev = START
    for step_fn in strategy.steps:
        node_name = step_fn.__name__
        builder.add_node(node_name, step_fn)
        builder.add_edge(prev, node_name)
        prev = node_name
    builder.add_edge(prev, END)

    return builder.compile(checkpointer=checkpointer)
