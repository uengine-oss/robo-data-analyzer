"""step3_scan_project — Framework 만 사용. Scan Agent 1회 invoke.

흐름:
1. ``collect_scanner_input`` — 매니페스트 + 확장자 빈도 + 카테고리별 파일 목록 수집.
2. ``run_scan_agent`` — Agent 호출 → ``ScanResult`` Pydantic.
3. ``state["scan_result"]`` 에 dict 로 저장 → step4_linking 가 사용.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from analysis_flow.step3_scan_project.scan_agent import run_scan_agent
from analysis_flow.step3_scan_project.scanner_input_collector import (
    collect_scanner_input,
)
from shared.llm.cost_budget import CostBudget
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.log.logger import log_error, log_info, log_warning

_STEP = "step3.scan"


async def scan_codebase_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Scan Agent 1회 invoke → ``state["scan_result"] = dict``."""
    cfg = step_config(config)
    project = cfg.get("codebase")
    run = cfg.get("run")
    cost_budget: CostBudget | None = cfg.get("cost_budget")
    stream = cfg.get("stream")

    if project is None or run is None or cost_budget is None:
        log_warning(_STEP, "config_missing", "project/run/cost_budget 누락 — step skip")
        return {"scan_result": {}}

    project_root = Path(project.root)
    started = time.monotonic()
    log_info(_STEP, "scanning", project=project.name, project_root=str(project_root))

    scanner = collect_scanner_input(project_root)
    log_info(
        _STEP, "scanned",
        manifest=bool(scanner.get("manifest_content")),
        ext_kinds=len(scanner.get("extensions_count") or {}),
        categories=len(scanner.get("scanner_categories") or {}),
    )

    log_info(_STEP, "agent_invoke", max_calls="(see middleware)")
    try:
        result = await run_scan_agent(
            project_root=project_root,
            scanner_input=scanner,
            run_dir=run.run_dir,
            cost_budget=cost_budget,
            functions=state.get("functions", []),
            tables=state.get("tables", []),
            stream_emit=stream.emit if stream is not None else None,
        )
    except ModelCallLimitExceeded as e:
        # scan 은 프로젝트당 1회 invoke — 호출 한도 소진 = 프로젝트 식별 실패.
        # 빈 결과로 조용히 넘기지 않는다 (그러면 R/W 링킹이 통째로 빈 채 "성공"처럼
        # 보임). 무엇을 해야 하는지 명확히 남기고 중단한다 (절대원칙 #3 — 조용한 실패 금지).
        log_error(
            _STEP, "scan_call_limit_exceeded", e,
            note="scan agent 가 LLM 호출 한도 소진 — 프로젝트 식별 실패. "
                 "ROBO_AGENT_SCAN_MAX_CALLS 상향 또는 스캐너 입력·프로젝트 점검 필요",
        )
        raise

    result_dict = result.model_dump()
    log_info(
        _STEP, "done",
        matched_skill=result_dict.get("matched_skill"),
        confidence=result_dict.get("confidence"),
        patterns=len(result_dict.get("table_access_patterns") or []),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"scan_result": result_dict}
