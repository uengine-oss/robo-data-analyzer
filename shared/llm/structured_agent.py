"""구조화 출력 에이전트 1회 invoke — Scan Agent · 잔여 패턴 루프 공용 헬퍼.

탐색 도구를 든 에이전트를 1회 invoke 해서 ``response_format`` Pydantic 모델로
받는 흐름은 — 에이전트 생성(``create_agent``) + 표준 미들웨어 + invoke 적재
(``begin/end_agent_invoke``) + structured 추출 — 모든 호출처에서 똑같다.
이 헬퍼 하나로 모은다 (호출처마다 복붙 금지).

호출처는 프롬프트·도구·출력 모델·user 메시지만 다르게 넘긴다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, TypeVar

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from shared.config.tool_messages import TOOL_MESSAGES
from shared.llm.cost_budget import CostBudget
from shared.llm.llm_client import build_llm_client
from shared.llm.middleware.standard_stack import build_standard_stack
from shared.logging_and_stream.prompt_logger import begin_agent_invoke, end_agent_invoke

_T = TypeVar("_T", bound=BaseModel)


async def run_structured_agent(
    *,
    agent_name: str,
    invoke_id: str,
    system_prompt: str,
    tools: list,
    response_format: type[_T],
    user_message: str,
    max_calls: int,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> _T:
    """탐색 도구 에이전트 1회 invoke → ``response_format`` 인스턴스.

    Args:
        agent_name: 로그·스트림 키 (``"scan"`` / ``"residual"`` 등).
        invoke_id: invoke 식별자 — 적재 파일명에 쓰임 (``"run"`` = 단일 파일).
        system_prompt: 시스템 지시 전문.
        tools: 에이전트 도구 목록.
        response_format: 출력 강제 Pydantic 모델.
        user_message: user 메시지 전문.
        max_calls: 이 invoke 의 LLM 호출 한도 (회로 차단기).
        run_dir: ``logs/analysis.run/latest/`` 경로.
        cost_budget: run 1회의 ``CostBudget``.
        stream_emit: 프론트 NDJSON 스트림 emit (옵션).

    Returns:
        ``response_format`` 인스턴스. structured_response 가 비면 마지막
        메시지 본문을 JSON 파싱한다.
    """
    llm_model = build_llm_client()
    middleware = build_standard_stack(
        agent_name=agent_name,
        max_calls=max_calls,
        run_dir=run_dir,
        cost_budget=cost_budget,
        llm_model=llm_model,
        stream_emit=stream_emit,
        tool_messages=TOOL_MESSAGES,
    )
    agent = create_agent(
        model=llm_model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        response_format=response_format,
    )

    # invoke 의 모든 turn 을 agent_<name>.json 에 누적. finally 로 finalize.
    begin_agent_invoke(agent_name=agent_name, invoke_id=invoke_id, system_prompt=system_prompt)
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_message)]})
    finally:
        end_agent_invoke()

    structured = result.get("structured_response")
    if isinstance(structured, response_format):
        return structured

    # fallback — 마지막 메시지 본문 JSON 파싱
    last_content = result.get("messages", [])[-1].content if result.get("messages") else "{}"
    return response_format.model_validate_json(str(last_content))
