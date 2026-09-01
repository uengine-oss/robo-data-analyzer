"""구조화 출력 에이전트 1회 invoke — Scan Agent · 잔여 패턴 루프 공용 헬퍼.

탐색 도구를 든 에이전트를 1회 invoke 해서 ``response_format`` Pydantic 모델로
받는 흐름은 — 에이전트 생성(``create_agent``) + 표준 미들웨어 + invoke 적재
(``begin/end_agent_invoke``) + structured 추출 — 모든 호출처에서 똑같다.
이 헬퍼 하나로 모은다 (호출처마다 복붙 금지).

호출처는 프롬프트·도구·출력 모델·user 메시지만 다르게 넘긴다.

**출력 양식 강제는 "마지막 도구 호출" 패턴**. ``response_format=Model`` 을 받으면
``_wrap_pydantic_as_submit_tool`` 가 그 모델을 args_schema 로 하는 도구를 만들어
``tools`` 마지막에 추가한다. agent 는 탐색 끝에 그 도구를 한 번 호출하고 ``Command``
가 결과를 state 에 적재 + agent loop 를 종료한다. LangChain 의 자동 ToolStrategy
(``response_format`` 을 합성 도구로 wrap + ``tool_choice="any"`` 강제) 진입을 차단
— qwen + SGLang 의 [도구 N + Pydantic schema] 동시 grammar mask 폭주 회피.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Annotated, Callable, Optional, TypeVar

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, ValidationError, create_model

from agents.tools.tool_messages import TOOL_MESSAGES
from llm.usage.cost_budget import CostBudget
from llm.llm_client import build_llm_client
from llm.repetition_guard import RepetitionGuardCallback
from llm.runners.model_output_error import model_output_kind
from agents.middleware.agent_stack import build_standard_stack
from llm.request_tracing import begin_agent_invoke, end_agent_invoke
from shared.config.app_settings import SETTINGS

_T = TypeVar("_T", bound=BaseModel)

# Agent state 에 최종 인스턴스를 적재할 표준 키 — 호출자가 result[_STATE_KEY] 로 받음.
_STATE_KEY = "structured_response"


class _CompactSubmitTool(StructuredTool):
    """Expose the compact response schema while retaining injected call identity."""

    @property
    def tool_call_schema(self) -> dict:
        schema = copy.deepcopy(self.get_input_schema().model_json_schema())
        properties = schema.get("properties") or {}
        properties.pop("tool_call_id", None)
        required = schema.get("required") or []
        schema["required"] = [name for name in required if name != "tool_call_id"]
        return schema


class AgentModelOutputError(RuntimeError):
    """Agent 모델이 usable submit 후보를 만들지 못함; 실행 배선 실패와 구분한다."""

    def __init__(self, message: str, *, kind: str = "schema") -> None:
        super().__init__(message)
        self.kind = kind


class StructuredResponseMissingError(AgentModelOutputError):
    """Agent가 최종 submit 도구 없이 종료해 구조화 결과가 없을 때 발생."""


class StructuredResponseValidationError(AgentModelOutputError):
    """Agent가 submit 도구를 호출했지만 payload가 출력 계약에 맞지 않을 때 발생."""


class StructuredResponseStateError(RuntimeError):
    """유효한 submit 호출 뒤 구조화 state가 사라진 내부 배선 오류."""


def _wrap_pydantic_as_submit_tool(
    response_format: type[_T],
    *,
    tool_name: str = "submit_result",
) -> Callable:
    """Pydantic 모델을 "탐색 끝에 정확히 한 번 호출할 출력 도구" 로 wrap.

    LangChain ``create_agent`` 에 ``response_format`` 인자로 Pydantic 모델을 넘기면
    LangChain 이 자동으로 ToolStrategy (response_format 을 합성 도구로 wrap +
    ``tool_choice="any"`` 강제) 를 적용하는데, 이 경로는 SGLang 의 xgrammar
    백엔드가 [도구 N + response_format schema] 를 동시에 마스킹하게 만들어
    qwen 에서 grammar decoder 가 폭주(hang)하는 사례가 있다.

    회피 = ``response_format`` 인자 안 넘기고 직접 도구로 노출. 양식 grammar 는
    도구 호출 시 한 번만 컴파일 (정상 케이스).

    Args:
        response_format: 출력 강제 Pydantic 모델.
        tool_name: 도구 이름 — LLM 에게 system prompt 로 안내될 이름.

    Returns:
        LangChain ``@tool`` 적용된 함수 — agent ``tools`` 마지막에 추가.
    """

    field_definitions = {}
    for name, field in response_format.model_fields.items():
        default = (
            ...
            if field.is_required()
            else field.default_factory()
            if field.default_factory is not None
            else field.default
        )
        field_definitions[name] = (field.rebuild_annotation(), default)
    submit_args = create_model(
        f"{response_format.__name__}SubmitArgs",
        __base__=response_format,
        **field_definitions,
        tool_call_id=(Annotated[str, InjectedToolCallId], ...),
    )

    def submit(**kwargs) -> Command:
        """탐색 끝 — 결과 양식 한 번 호출. agent loop 명시 종료."""
        tool_call_id = kwargs.pop("tool_call_id")
        instance = response_format(**kwargs)
        return Command(
            update={
                _STATE_KEY: instance,
                "messages": [ToolMessage("ok", tool_call_id=tool_call_id)],
            },
            goto="__end__",
        )

    submit.__name__ = tool_name

    return _CompactSubmitTool.from_function(
        func=submit,
        name=tool_name,
        args_schema=submit_args,
        return_direct=True,
    )


async def run_structured_agent(
    *,
    agent_name: str,
    invoke_id: str,
    system_prompt: str,
    tools: list,
    response_format: type[_T],
    user_message: str,
    run_dir: Path,
    cost_budget: CostBudget,
    stream_emit: Optional[Callable[[dict], None]] = None,
) -> _T:
    """탐색 도구 에이전트 1회 invoke → ``response_format`` 인스턴스.

    Args:
        agent_name: 로그·스트림 키 (``"scan"`` / ``"residual"`` 등).
        invoke_id: invoke 식별자 — 적재 파일명에 쓰임 (``"run"`` = 단일 파일).
        system_prompt: 시스템 지시 전문. 탐색 끝에 ``submit_result`` 도구를
            정확히 한 번 호출하라는 안내를 포함해야 한다.
        tools: 에이전트 탐색 도구 목록 — 출력 도구는 자동 추가.
        response_format: 출력 강제 Pydantic 모델 — submit 도구로 wrap 된다.
        user_message: user 메시지 전문.
        run_dir: run 로그 디렉토리 (``logs/``).
        cost_budget: run 1회의 ``CostBudget``.
        stream_emit: 프론트 NDJSON 스트림 emit (옵션).

    Returns:
        ``response_format`` 인스턴스. agent 가 submit 도구를 호출하지 않고 끝나면
        ``ValueError`` (silent 실패 금지 — 표면화).
    """
    guard_on = SETTINGS.analysis_llm.repetition_guard
    llm_model = build_llm_client(streaming=guard_on)
    middleware = build_standard_stack(
        agent_name=agent_name,
        run_dir=run_dir,
        cost_budget=cost_budget,
        stream_emit=stream_emit,
        tool_messages=TOOL_MESSAGES,
    )
    submit_tool = _wrap_pydantic_as_submit_tool(
        response_format, tool_name=f"submit_{agent_name}",
    )
    agent = create_agent(
        model=llm_model,
        tools=[*tools, submit_tool],
        system_prompt=system_prompt,
        middleware=middleware,
        # response_format 인자 안 넘김 — LangChain ToolStrategy 자동 진입 차단.
    )

    # invoke 의 모든 turn 을 agent_<name>.json 에 누적. finally 로 finalize.
    begin_agent_invoke(
        agent_name=agent_name,
        invoke_id=invoke_id,
        system_prompt=system_prompt,
    )
    invoke_config = (
        {"callbacks": [RepetitionGuardCallback(f"agent:{agent_name}")]} if guard_on else None
    )
    submit_tool_name = f"submit_{agent_name}"
    try:
        try:
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_message)]}, config=invoke_config
            )
        except Exception as error:
            kind = model_output_kind(error)
            if kind is not None:
                raise AgentModelOutputError(
                    f"agent '{agent_name}' usable submit 미산출: {type(error).__name__}: {error}",
                    kind=kind,
                ) from error
            raise
    finally:
        end_agent_invoke()

    instance = result.get(_STATE_KEY)
    if isinstance(instance, response_format):
        return instance

    submit_calls = [
        call
        for message in result.get("messages", [])
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call.get("name") == submit_tool_name
    ]
    if submit_calls:
        try:
            response_format.model_validate(submit_calls[-1].get("args") or {})
        except ValidationError as error:
            coordinates = [
                ".".join(str(part) for part in item["loc"])
                for item in error.errors(include_input=False)
            ]
            raise StructuredResponseValidationError(
                f"agent '{agent_name}' {submit_tool_name} payload schema 위반: "
                f"{', '.join(coordinates)}"
            ) from error
        raise StructuredResponseStateError(
            f"agent '{agent_name}' {submit_tool_name} payload는 유효하지만 "
            f"'{_STATE_KEY}' state가 보존되지 않음"
        )

    raise StructuredResponseMissingError(
        f"agent '{agent_name}' invoke 종료 — {submit_tool_name} 호출 누락 "
        f"system prompt 의 '도구 호출 안내' 가 모델에게 명확히 전달됐는지 확인."
    )
