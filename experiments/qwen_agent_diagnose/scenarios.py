"""5 시나리오 격리 시험 — agent hang 원인 영역 좁히기.

가설: agent 의 (tool calling) + (structured response_format) 결합이 SGLang 의
generation 을 멈추게 함. 두 강제를 하나씩 빼서 어느 옵션에서 hang 사라지는지 확인.

| # | tool | response_format | 입력 토큰 | 기대 |
|---|------|-----------------|----------|------|
| A | OFF | OFF | LARGE (8K) | 정상 — LLM 자체 동작 (기준선) |
| B | ON  | OFF | LARGE (8K) | tool 만 hang? |
| C | ON  | ON  | LARGE (8K) | 현재 hang 재현 (기준) |
| D | OFF | ON  | LARGE (8K) | response_format 만 hang? |
| E | ON  | ON  | SMALL (1K) | 입력 크기 영향? |

각 시나리오는 ``ScenarioResult`` 반환 — diagnose.py 가 jsonl 로 적재.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from experiments.qwen_agent_diagnose.prompts import LARGE_INPUT, SMALL_INPUT, SYSTEM_PROMPT
from shared.llm.llm_client import build_llm_client


# ── 출력 모델 (response_format 용) ─────────────────────────────


class MiniSummaryOutput(BaseModel):
    """분석 본체의 SummaryOutput 미니 버전 — 같은 strict 패턴 재현."""
    summary: str = Field(description="함수 의미 한 단락")
    key_points: list[str] = Field(default_factory=list, description="핵심 키포인트")
    stereotype: str = Field(description="함수 역할 라벨")


# ── 도구 (tool calling 용) ────────────────────────────────────


@tool
def echo_tool(message: str) -> str:
    """단순 echo 도구 — tool calling 동작만 검증 (도메인 무관)."""
    return f"echo: {message}"


@tool
def count_tokens_tool(text: str) -> int:
    """텍스트 토큰 수 추정 도구 — agent 가 호출 가능."""
    return len(text) // 4


_TOOLS = [echo_tool, count_tokens_tool]


# ── 결과 데이터클래스 ─────────────────────────────────────────


@dataclass
class ScenarioResult:
    """1 시나리오 1회 시도 결과."""
    scenario: str                    # "A" / "B" / "C" / "D" / "E"
    tool_calling: bool
    response_format: bool
    input_tokens_estimate: int       # 입력 글자수 / 4
    status: str                      # "success" / "timeout" / "error" / "empty_response"
    elapsed_sec: float
    response_length: int = 0         # 응답 글자수 (0 = empty / hang 의심)
    output_preview: str = ""         # 응답 앞 200자
    error_type: str = ""
    error_message: str = ""
    notes: dict[str, Any] = field(default_factory=dict)


# ── 헬퍼 ──────────────────────────────────────────────────────


def _input_tokens(text: str) -> int:
    """입력 글자수 / 4 로 토큰 수 추정."""
    return len(text) // 4


async def _ainvoke_with_timing(
    llm: Any, messages: list, timeout_sec: float, *, structured: bool = False,
) -> tuple[str, float, int]:
    """본체 패턴 (await llm.ainvoke) + 응답 길이·시간 측정.

    본체 `yaml_llm_runner.py` 가 `await structured.ainvoke(messages)` 사용 — 본체 hang
    을 정확히 재현하려면 stream 아닌 ainvoke 로 측정해야 한다.

    Args:
        structured: True 면 응답이 `{'raw', 'parsed', 'parsing_error'}` dict
                    (include_raw=True 의 with_structured_output 결과).

    Returns:
        (응답 텍스트, elapsed sec, 응답 글자수). timeout 시 asyncio.TimeoutError.
    """
    start = time.monotonic()
    response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout_sec)
    elapsed = time.monotonic() - start

    if structured:
        # with_structured_output(include_raw=True) → {'raw', 'parsed', 'parsing_error'}
        parsed = response.get("parsed") if isinstance(response, dict) else None
        raw = response.get("raw") if isinstance(response, dict) else None
        if parsed is not None:
            text = str(parsed.model_dump() if hasattr(parsed, "model_dump") else parsed)
        elif raw is not None:
            text = getattr(raw, "content", "") or ""
        else:
            text = ""
    else:
        # 단순 ainvoke → BaseMessage (or AIMessage)
        text = getattr(response, "content", "") or ""
        if not isinstance(text, str):
            text = str(text)
        # tool_calls 있으면 같이 측정 (B 시나리오 — text 없을 수도)
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls and not text:
            text = f"<tool_calls: {len(tool_calls)}건> {tool_calls}"
    return text, elapsed, len(text)


# ── 시나리오 함수 ─────────────────────────────────────────────


async def run_scenario_a(timeout_sec: float = 60.0) -> ScenarioResult:
    """A — 단순 호출 (tool OFF / response_format OFF). 기준선 — LLM 자체 동작 확인."""
    result = ScenarioResult(
        scenario="A", tool_calling=False, response_format=False,
        input_tokens_estimate=_input_tokens(SYSTEM_PROMPT + LARGE_INPUT),
        status="pending", elapsed_sec=0.0,
    )
    start = time.monotonic()
    try:
        llm = build_llm_client()
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=LARGE_INPUT)]
        text, elapsed, length = await _ainvoke_with_timing(llm, messages, timeout_sec)
        result.elapsed_sec = elapsed
        result.response_length = length
        result.output_preview = text[:200]
        result.status = "success" if length > 0 else "empty_response"
    except asyncio.TimeoutError:
        result.elapsed_sec = time.monotonic() - start
        result.status = "timeout"
        result.error_type = "TimeoutError"
        result.error_message = f"{timeout_sec}s 안에 응답 종료 X"
    except Exception as e:
        result.elapsed_sec = time.monotonic() - start
        result.status = "error"
        result.error_type = type(e).__name__
        result.error_message = str(e)[:500]
    return result


async def run_scenario_b(timeout_sec: float = 60.0) -> ScenarioResult:
    """B — tool 만 ON / response_format OFF. tool calling 자체가 hang 원인인지."""
    result = ScenarioResult(
        scenario="B", tool_calling=True, response_format=False,
        input_tokens_estimate=_input_tokens(SYSTEM_PROMPT + LARGE_INPUT),
        status="pending", elapsed_sec=0.0,
    )
    start = time.monotonic()
    try:
        llm = build_llm_client()
        llm_with_tools = llm.bind_tools(_TOOLS)
        messages = [
            SystemMessage(content=SYSTEM_PROMPT + "\n\n도구 사용 가능. 필요 시 호출 후 자유 텍스트 응답."),
            HumanMessage(content=LARGE_INPUT),
        ]
        text, first_token, chunks = await _stream_with_first_token(llm_with_tools, messages, timeout_sec)
        result.elapsed_sec = time.monotonic() - start
        result.first_token_sec = first_token
        result.chunk_count = chunks
        result.output_preview = text[:200]
        result.status = "success" if (text or chunks > 0) else "empty_response"
    except asyncio.TimeoutError:
        result.elapsed_sec = time.monotonic() - start
        result.status = "timeout"
        result.error_type = "TimeoutError"
        result.error_message = f"{timeout_sec}s 안에 응답 종료 X"
    except Exception as e:
        result.elapsed_sec = time.monotonic() - start
        result.status = "error"
        result.error_type = type(e).__name__
        result.error_message = str(e)[:500]
    return result


async def run_scenario_c(timeout_sec: float = 60.0) -> ScenarioResult:
    """C — tool ON + response_format ON. **현재 분석 본체의 hang 패턴 재현**."""
    result = ScenarioResult(
        scenario="C", tool_calling=True, response_format=True,
        input_tokens_estimate=_input_tokens(SYSTEM_PROMPT + LARGE_INPUT),
        status="pending", elapsed_sec=0.0,
        notes={"hint": "분석 본체의 structured_agent 패턴 (tool + response_format)"},
    )
    start = time.monotonic()
    try:
        llm = build_llm_client()
        llm_with_tools = llm.bind_tools(_TOOLS)
        structured = llm_with_tools.with_structured_output(MiniSummaryOutput, include_raw=True)
        messages = [
            SystemMessage(content=SYSTEM_PROMPT + "\n\n도구 사용 가능 + MiniSummaryOutput Pydantic 응답 강제."),
            HumanMessage(content=LARGE_INPUT),
        ]
        text, first_token, chunks = await _stream_with_first_token(structured, messages, timeout_sec)
        result.elapsed_sec = time.monotonic() - start
        result.first_token_sec = first_token
        result.chunk_count = chunks
        result.output_preview = text[:200]
        result.status = "success" if text else "empty_response"
    except asyncio.TimeoutError:
        result.elapsed_sec = time.monotonic() - start
        result.status = "timeout"
        result.error_type = "TimeoutError"
        result.error_message = f"{timeout_sec}s 안에 응답 종료 X"
    except Exception as e:
        result.elapsed_sec = time.monotonic() - start
        result.status = "error"
        result.error_type = type(e).__name__
        result.error_message = str(e)[:500]
    return result


async def run_scenario_d(timeout_sec: float = 60.0) -> ScenarioResult:
    """D — tool OFF / response_format ON. response_format(structured output) 만이 hang 원인인지."""
    result = ScenarioResult(
        scenario="D", tool_calling=False, response_format=True,
        input_tokens_estimate=_input_tokens(SYSTEM_PROMPT + LARGE_INPUT),
        status="pending", elapsed_sec=0.0,
        notes={"hint": "yaml_llm_runner 패턴 (response_format 만)"},
    )
    start = time.monotonic()
    try:
        llm = build_llm_client()
        structured = llm.with_structured_output(MiniSummaryOutput, include_raw=True)
        messages = [
            SystemMessage(content=SYSTEM_PROMPT + "\n\nMiniSummaryOutput Pydantic 응답 강제."),
            HumanMessage(content=LARGE_INPUT),
        ]
        text, first_token, chunks = await _stream_with_first_token(structured, messages, timeout_sec)
        result.elapsed_sec = time.monotonic() - start
        result.first_token_sec = first_token
        result.chunk_count = chunks
        result.output_preview = text[:200]
        result.status = "success" if text else "empty_response"
    except asyncio.TimeoutError:
        result.elapsed_sec = time.monotonic() - start
        result.status = "timeout"
        result.error_type = "TimeoutError"
        result.error_message = f"{timeout_sec}s 안에 응답 종료 X"
    except Exception as e:
        result.elapsed_sec = time.monotonic() - start
        result.status = "error"
        result.error_type = type(e).__name__
        result.error_message = str(e)[:500]
    return result


async def run_scenario_e(timeout_sec: float = 60.0) -> ScenarioResult:
    """E — C 와 동일 (tool + response_format) + 입력 SMALL (1K). 입력 크기 영향."""
    result = ScenarioResult(
        scenario="E", tool_calling=True, response_format=True,
        input_tokens_estimate=_input_tokens(SYSTEM_PROMPT + SMALL_INPUT),
        status="pending", elapsed_sec=0.0,
        notes={"hint": "C 와 동일 옵션 + 입력 크기 1/8 (입력 크기 영향 영역 좁히기)"},
    )
    start = time.monotonic()
    try:
        llm = build_llm_client()
        llm_with_tools = llm.bind_tools(_TOOLS)
        structured = llm_with_tools.with_structured_output(MiniSummaryOutput, include_raw=True)
        messages = [
            SystemMessage(content=SYSTEM_PROMPT + "\n\n도구 사용 가능 + MiniSummaryOutput Pydantic 응답 강제."),
            HumanMessage(content=SMALL_INPUT),
        ]
        text, first_token, chunks = await _stream_with_first_token(structured, messages, timeout_sec)
        result.elapsed_sec = time.monotonic() - start
        result.first_token_sec = first_token
        result.chunk_count = chunks
        result.output_preview = text[:200]
        result.status = "success" if text else "empty_response"
    except asyncio.TimeoutError:
        result.elapsed_sec = time.monotonic() - start
        result.status = "timeout"
        result.error_type = "TimeoutError"
        result.error_message = f"{timeout_sec}s 안에 응답 종료 X"
    except Exception as e:
        result.elapsed_sec = time.monotonic() - start
        result.status = "error"
        result.error_type = type(e).__name__
        result.error_message = str(e)[:500]
    return result


# 시나리오 선택 dict — diagnose.py 가 CLI 옵션으로 매핑.
SCENARIOS: dict[str, Any] = {
    "A": run_scenario_a,
    "B": run_scenario_b,
    "C": run_scenario_c,
    "D": run_scenario_d,
    "E": run_scenario_e,
}


def result_to_jsonl_dict(result: ScenarioResult) -> dict:
    """jsonl 적재용 dict 변환 (notes 의 nested dict 보존)."""
    return asdict(result)
