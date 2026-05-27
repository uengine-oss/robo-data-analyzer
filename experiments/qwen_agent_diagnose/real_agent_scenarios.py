"""실제 본체 정확 재현 시험 — 7 시나리오 (R1~R7) 로 hang 원인 영역 좁히기.

본체 `analysis_flow/step3_scan_project/scan_agent.py` 의 `run_scan_agent` 패턴
정확 재현 + 옵션 토글:
- tools         : 본체 9개 ↔ 빈 리스트
- response_format: ScanResult ↔ None (자유 텍스트)
- invoke 방식   : create_agent ↔ llm.with_structured_output ↔ 단순 llm.ainvoke
- 입력 크기     : 실제 data/source ↔ 작은 mock

본체 코드 (`_build_scan_tools`, `_build_user_message`, `_SYSTEM_PROMPT_PATH`,
`build_standard_stack`) 를 그대로 import. 격리 보증 X — 정확 재현 우선.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage

from agent_tools.agent_output_models import ScanResult
from analysis_flow.step1_parse_ddl.parse_ddl_step import parse_ddl_step
from analysis_flow.step2_load_ast.load_ast_step import load_ast_step
from analysis_flow.step3_scan_project.scan_agent import (
    _SYSTEM_PROMPT_PATH,
    _build_scan_tools,
    _build_user_message,
)
from cli_scripts.strategy_detector import detect_strategy
from data_models.graph_nodes import Function, Table
from data_models.project_and_run import Project
from shared.config.env_catalog import ENV
from shared.config.tool_messages import TOOL_MESSAGES
from shared.llm.cost_budget import CostBudget
from shared.llm.llm_client import build_llm_client
from shared.llm.middleware.standard_stack import build_standard_stack
from shared.logging_and_stream.logger import log_info
from shared.logging_and_stream.prompt_logger import begin_agent_invoke, end_agent_invoke


_EXPERIMENT_ROOT = Path("c:/tmp/qwen_diagnose_runs")

# step1+step2 실제 결과 캐시 — 시나리오마다 같은 functions/tables 사용 (시험 일관성).
_PREPARED_DATA: Optional[tuple[list[Function], list[Table]]] = None


class _NoopNeo4jClient:
    """no-op Neo4jClient — step1/step2 의 upsert 호출 받지만 실제 Neo4j 안 띄움.

    본체 step1 (parse_ddl_step) 이 ``project + neo_client`` 둘 다 필수라 skip 방지.
    upsert 결과는 안 보지만 functions/tables 등 state 결과는 정상 누적됨.
    """
    async def run_batch_unwind(self, *args, **kwargs):
        return None

    async def run_query(self, *args, **kwargs):
        return None


@dataclass
class RealScenarioResult:
    """본체 정확 재현 시험 1회 결과."""
    scenario: str                      # "R1" ~ "R7"
    description: str
    use_tools: bool
    use_response_format: bool
    invoke_mode: str                   # "agent" / "structured" / "simple"
    input_kind: str                    # "real_data_source" / "mock_small"
    input_tokens_estimate: int
    status: str                        # "success" / "timeout" / "error" / "empty_response"
    elapsed_sec: float
    response_length: int = 0
    output_preview: str = ""
    error_type: str = ""
    error_message: str = ""
    notes: dict[str, Any] = field(default_factory=dict)


# ── 입력 준비 ──────────────────────────────────────────────────


def _resolve_project_root() -> Path:
    """실제 data/source 디렉토리. ROBO_DATA_DIR/source/<sub> 또는 그 직속."""
    source = ENV.data.dir / "source"
    if not source.is_dir():
        raise FileNotFoundError(f"data source 디렉토리 없음: {source}")
    subdirs = [p for p in source.iterdir() if p.is_dir()]
    if subdirs:
        return sorted(subdirs)[0]   # 첫 sub 프로젝트
    return source                    # source/ 직속에 소스 파일


def _scanner_input(project_root: Path) -> dict:
    """본체 scanner_input 형식 — extensions_count + manifest + categories."""
    counts: dict[str, int] = {}
    for f in project_root.rglob("*"):
        if f.is_file():
            ext = f.suffix.lower().lstrip(".")
            if ext:
                counts[ext] = counts.get(ext, 0) + 1
    # 상위 확장자 5개만
    top = dict(sorted(counts.items(), key=lambda kv: -kv[1])[:5])
    return {
        "extensions_count": top,
        "manifest_content": "",
        "scanner_categories": list(top.keys()),
    }


def _ensure_run_dir(scenario: str) -> Path:
    """시나리오별 임시 run_dir — 본체 prompt_logger 가 적재."""
    run_dir = _EXPERIMENT_ROOT / scenario
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


async def _ensure_prepared_data(project_root: Path) -> tuple[list[Function], list[Table]]:
    """본체 step1 (parse_ddl) + step2 (load_ast) 그대로 실행 → (functions, tables).

    한 번 실행한 결과를 모듈 캐시 — 시나리오마다 같은 데이터 사용 (실행 시간 절감 +
    시험 일관성). 본체 cli_scripts/analyze_data_dir.py 의 step1+step2 와 정확히 같은
    호출 패턴.
    """
    global _PREPARED_DATA
    if _PREPARED_DATA is not None:
        return _PREPARED_DATA

    strategy_name = detect_strategy(project_root)
    ddl_dir = ENV.data.dir / "ddl"
    project = Project(
        name=project_root.name,
        root=project_root,
        strategy=strategy_name,  # type: ignore[arg-type]
        ddl_dir=ddl_dir if ddl_dir.is_dir() else None,
    )
    # config = configurable dict. no-op neo_client → step1 skip 방지 + Neo4j 안 띄움.
    config = {"configurable": {"project": project, "neo_client": _NoopNeo4jClient()}}

    log_info("diag.prepare", "step1_parse_ddl_start", project=project.name)
    state: dict = {}
    state.update(await parse_ddl_step(state, config))
    log_info("diag.prepare", "step2_load_ast_start", tables=len(state.get("tables", [])))
    state.update(await load_ast_step(state, config))

    functions = state.get("functions", [])
    tables = state.get("tables", [])
    log_info(
        "diag.prepare", "done",
        functions=len(functions), tables=len(tables),
        columns=len(state.get("columns", [])),
    )

    _PREPARED_DATA = (functions, tables)
    return _PREPARED_DATA


# ── 핵심 디스패치 함수 ────────────────────────────────────────


async def _run_real_scenario(
    *,
    scenario: str,
    description: str,
    use_tools: bool,
    use_response_format: bool,
    invoke_mode: str,
    use_small_input: bool,
    timeout_sec: float,
) -> RealScenarioResult:
    """1 시나리오 본체 재현 시도 → RealScenarioResult.

    invoke_mode:
        "agent"      — create_agent(model, tools, system, middleware, response_format)
        "structured" — llm.with_structured_output(response_format)  (agent 없음)
        "simple"     — llm 단순 ainvoke (agent / format 둘 다 없음)
    """
    # 입력 준비 (실제 또는 mock)
    try:
        project_root = _resolve_project_root()
    except FileNotFoundError as e:
        return RealScenarioResult(
            scenario=scenario, description=description,
            use_tools=use_tools, use_response_format=use_response_format,
            invoke_mode=invoke_mode,
            input_kind="real_data_source",
            input_tokens_estimate=0,
            status="error", elapsed_sec=0.0,
            error_type="FileNotFoundError", error_message=str(e),
        )

    scanner_input = _scanner_input(project_root)
    # 본체와 100% 동일 — step1 + step2 실제 결과 사용 (캐시).
    functions, tables = await _ensure_prepared_data(project_root)

    # user message: 실제 본체 _build_user_message 또는 작은 mock
    if use_small_input:
        user_message = "위 단서로 프로젝트 성격을 간단히 식별해 ScanResult 를 출력하라."
        input_kind = "mock_small"
    else:
        user_message = _build_user_message(project_root, scanner_input, tables)
        input_kind = "real_data_source"

    system_prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    input_tokens_estimate = (len(system_prompt) + len(user_message)) // 4

    result = RealScenarioResult(
        scenario=scenario, description=description,
        use_tools=use_tools, use_response_format=use_response_format,
        invoke_mode=invoke_mode,
        input_kind=input_kind,
        input_tokens_estimate=input_tokens_estimate,
        status="pending", elapsed_sec=0.0,
        notes={"project_root": str(project_root)},
    )

    # 실행
    start = time.monotonic()
    try:
        if invoke_mode == "agent":
            text = await asyncio.wait_for(
                _invoke_agent(
                    scenario, system_prompt, user_message,
                    use_tools=use_tools, use_response_format=use_response_format,
                    project_root=project_root, functions=functions, tables=tables,
                ),
                timeout=timeout_sec,
            )
        elif invoke_mode == "structured":
            text = await asyncio.wait_for(
                _invoke_structured(system_prompt, user_message),
                timeout=timeout_sec,
            )
        elif invoke_mode == "simple":
            text = await asyncio.wait_for(
                _invoke_simple(system_prompt, user_message),
                timeout=timeout_sec,
            )
        else:
            raise ValueError(f"Unknown invoke_mode: {invoke_mode}")

        result.elapsed_sec = time.monotonic() - start
        result.response_length = len(text or "")
        result.output_preview = (text or "")[:300]
        result.status = "success" if result.response_length > 0 else "empty_response"
    except asyncio.TimeoutError:
        result.elapsed_sec = time.monotonic() - start
        result.status = "timeout"
        result.error_type = "TimeoutError"
        result.error_message = f"{timeout_sec}s 안에 응답 종료 X (hang 확정)"
    except Exception as e:
        result.elapsed_sec = time.monotonic() - start
        result.status = "error"
        result.error_type = type(e).__name__
        result.error_message = str(e)[:500]

    return result


# ── invoke 모드별 헬퍼 ────────────────────────────────────────


async def _invoke_agent(
    scenario: str,
    system_prompt: str,
    user_message: str,
    *,
    use_tools: bool,
    use_response_format: bool,
    project_root: Path,
    functions: list,
    tables: list,
) -> str:
    """본체 run_structured_agent 패턴 정확 재현 — tools/response_format 옵션 토글."""
    llm_model = build_llm_client()
    run_dir = _ensure_run_dir(scenario)
    cost_budget = CostBudget(limit_usd=999.0)   # 큰 한도 — 차단 X (실험용)

    tools = _build_scan_tools(project_root, functions, tables) if use_tools else []
    response_format = ScanResult if use_response_format else None

    middleware = build_standard_stack(
        agent_name=f"diag_{scenario}",
        max_calls=ENV.agent.scan_max_calls,
        run_dir=run_dir,
        cost_budget=cost_budget,
        llm_model=llm_model,
        stream_emit=None,
        tool_messages=TOOL_MESSAGES,
    )
    agent_kwargs: dict[str, Any] = dict(
        model=llm_model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
    )
    if response_format is not None:
        agent_kwargs["response_format"] = response_format

    agent = create_agent(**agent_kwargs)

    begin_agent_invoke(
        agent_name=f"diag_{scenario}", invoke_id="run", system_prompt=system_prompt,
    )
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_message)]})
    finally:
        end_agent_invoke()

    # 결과 추출 — structured 면 structured_response, 아니면 마지막 메시지
    if use_response_format:
        structured = result.get("structured_response")
        if structured is not None:
            return json.dumps(structured.model_dump(), ensure_ascii=False)[:1000]
    last_messages = result.get("messages") or []
    if last_messages:
        last_content = last_messages[-1].content
        return str(last_content)[:1000]
    return ""


async def _invoke_structured(system_prompt: str, user_message: str) -> str:
    """llm.with_structured_output 단독 (agent 없음, 단순 structured 호출)."""
    llm = build_llm_client()
    structured = llm.with_structured_output(ScanResult, include_raw=True)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
    result = await structured.ainvoke(messages)
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if parsed is not None:
        return json.dumps(parsed.model_dump(), ensure_ascii=False)[:1000]
    raw = result.get("raw") if isinstance(result, dict) else None
    if raw is not None:
        return str(getattr(raw, "content", ""))[:1000]
    return ""


async def _invoke_simple(system_prompt: str, user_message: str) -> str:
    """단순 llm.ainvoke — agent / response_format 둘 다 없음. 대조군."""
    llm = build_llm_client()
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
    response = await llm.ainvoke(messages)
    content = getattr(response, "content", "")
    return str(content)[:1000]


# ── 7 시나리오 함수 ───────────────────────────────────────────


async def run_r1(timeout_sec: float = 300.0) -> RealScenarioResult:
    """R1 — 본체 그대로 (tools=9 / format=ScanResult / mode=agent / input=실제). hang 재현 기준."""
    return await _run_real_scenario(
        scenario="R1", description="본체 그대로 (현재 hang 패턴)",
        use_tools=True, use_response_format=True, invoke_mode="agent",
        use_small_input=False, timeout_sec=timeout_sec,
    )


async def run_r2(timeout_sec: float = 300.0) -> RealScenarioResult:
    """R2 — tools=9 / format=None / agent / 실제. response_format 만 빼봄."""
    return await _run_real_scenario(
        scenario="R2", description="response_format 만 제거 (tool calling 유지)",
        use_tools=True, use_response_format=False, invoke_mode="agent",
        use_small_input=False, timeout_sec=timeout_sec,
    )


async def run_r3(timeout_sec: float = 300.0) -> RealScenarioResult:
    """R3 — tools=[] / format=ScanResult / agent / 실제. tools 만 빼봄."""
    return await _run_real_scenario(
        scenario="R3", description="tools 만 제거 (response_format 유지)",
        use_tools=False, use_response_format=True, invoke_mode="agent",
        use_small_input=False, timeout_sec=timeout_sec,
    )


async def run_r4(timeout_sec: float = 300.0) -> RealScenarioResult:
    """R4 — tools=[] / format=None / agent / 실제. agent 골격만."""
    return await _run_real_scenario(
        scenario="R4", description="agent 골격만 (tools/format 둘 다 제거)",
        use_tools=False, use_response_format=False, invoke_mode="agent",
        use_small_input=False, timeout_sec=timeout_sec,
    )


async def run_r5(timeout_sec: float = 300.0) -> RealScenarioResult:
    """R5 — llm.with_structured_output / 실제. agent 폐기 + structured 만."""
    return await _run_real_scenario(
        scenario="R5", description="agent 폐기 + with_structured_output 단독",
        use_tools=False, use_response_format=True, invoke_mode="structured",
        use_small_input=False, timeout_sec=timeout_sec,
    )


async def run_r6(timeout_sec: float = 300.0) -> RealScenarioResult:
    """R6 — 단순 llm.ainvoke / 실제. 완전 단순 (대조군)."""
    return await _run_real_scenario(
        scenario="R6", description="완전 단순 ainvoke (대조군)",
        use_tools=False, use_response_format=False, invoke_mode="simple",
        use_small_input=False, timeout_sec=timeout_sec,
    )


async def run_r7(timeout_sec: float = 300.0) -> RealScenarioResult:
    """R7 — 본체 그대로 + 작은 mock 입력. 입력 크기 영향."""
    return await _run_real_scenario(
        scenario="R7", description="본체 그대로 + 작은 mock 입력 (입력 크기 영향)",
        use_tools=True, use_response_format=True, invoke_mode="agent",
        use_small_input=True, timeout_sec=timeout_sec,
    )


REAL_SCENARIOS: dict[str, Any] = {
    "R1": run_r1,
    "R2": run_r2,
    "R3": run_r3,
    "R4": run_r4,
    "R5": run_r5,
    "R6": run_r6,
    "R7": run_r7,
}


def real_result_to_jsonl_dict(result: RealScenarioResult) -> dict:
    """jsonl 적재용 dict 변환."""
    return asdict(result)
