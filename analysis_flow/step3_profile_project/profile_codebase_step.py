"""step3_profile_project — Framework 만 사용. Profile Agent 1회 invoke.

흐름:
1. ``collect_scanner_input`` — 매니페스트 + 확장자 빈도 + 카테고리별 파일 목록 수집.
2. ``run_profile_agent`` — Agent 호출 → ``ProfileResult`` Pydantic.
3. ``state["profile_result"]`` 에 dict 로 저장 → step4_linking 가 사용.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step3_profile_project.profile_agent import run_profile_agent
from analysis_flow.step3_profile_project.scanner_input_collector import (
    collect_scanner_input,
)
from shared.llm.cost_budget import CostBudget
from shared.logging_and_stream.logger import log_info, log_warning

_STEP = "step3.profile"


async def profile_codebase_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Profile Agent 1회 invoke → ``state["profile_result"] = dict``."""
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    project = cfg.get("project")
    run = cfg.get("run")
    cost_budget: CostBudget | None = cfg.get("cost_budget")
    stream = cfg.get("stream")

    if project is None or run is None or cost_budget is None:
        log_warning(_STEP, "config_missing", "project/run/cost_budget 누락 — step skip")
        return {"profile_result": {}}

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
    result = await run_profile_agent(
        project_root=project_root,
        scanner_input=scanner,
        run_dir=run.run_dir,
        cost_budget=cost_budget,
        stream_emit=stream.emit if stream is not None else None,
    )

    result_dict = result.model_dump()
    log_info(
        _STEP, "done",
        matched_skill=result_dict.get("matched_skill"),
        confidence=result_dict.get("confidence"),
        patterns=len(result_dict.get("table_access_patterns") or []),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"profile_result": result_dict}
