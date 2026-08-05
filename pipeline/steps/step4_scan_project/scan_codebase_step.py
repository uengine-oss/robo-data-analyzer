"""step4_scan_project — Framework 만 사용. Scan Agent 1회 invoke.

흐름:
1. ``collect_scanner_input`` — 매니페스트 + 확장자 빈도 + 카테고리별 파일 목록 수집.
2. ``run_scan_agent`` — Agent 호출 → ``ScanResult`` Pydantic.
3. ``state["scan_result"]`` 에 dict 로 저장 → step5_linking 가 사용.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from agents.scan import run_scan_agent
from agents.contracts import ScanResult
from agents.runtime import AgentModelOutputError

from pipeline.steps.step4_scan_project.scanner_input_collector import (
    collect_scanner_input,
)
from llm.usage.cost_budget import CostBudget
from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.models.analysis_finding import AnalysisFinding
from shared.events.runtime_logger import log_error, log_info, log_warning
from pipeline.execution.graph_delta_stream import emit_message

_STEP = "step4.scan"

async def scan_codebase_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Scan Agent 1회 invoke → ``state["scan_result"] = dict``."""
    cfg = step_config(config)
    project = cfg.get("codebase")
    run = cfg.get("run")
    cost_budget: CostBudget | None = cfg.get("cost_budget")
    stream = cfg.get("stream")

    if project is None or run is None or cost_budget is None:
        raise RuntimeError("scan_codebase requires project, run, and cost_budget")

    project_root = Path(project.root)
    started = time.monotonic()
    log_info(_STEP, "scanning", project_root=str(project_root))

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
            code_nodes=state.get("code_nodes", []),
            tables=state.get("tables", []),
            stream_emit=stream.emit if stream is not None else None,
        )
    except AgentModelOutputError as error:
        reason = f"{type(error).__name__}: {error}"
        record_analysis_findings([AnalysisFinding(
            concern="project_scan",
            status="unavailable",
            target_id=str(project_root),
            reasons=(reason,),
            evidence_rule="scan_agent",
        )])
        log_error(
            _STEP, "scan_model_output_unavailable", error,
            note="검증되지 않은 scan 후보는 적용하지 않고 downstream을 명시적 미가용 상태로 계속함",
        )
        result = ScanResult(
            analysis_status="unavailable",
            confidence=0.0,
            evidence="scan model output unavailable; see run evidence",
            evidence_locations=[],
            no_match_reason=reason,
        )

    result_dict = result.model_dump()
    unavailable = result_dict.get("analysis_status") == "unavailable"
    emit_message(
        stream,
        ("⚠️ 코드베이스 스캔 미가용 — 검증되지 않은 패턴 0건 적용"
         if unavailable else
         f"✅ 코드베이스 스캔 완료 — 패턴 {len(result_dict.get('table_access_patterns') or [])}"),
    )
    fields = {
        "matched_skill": result_dict.get("matched_skill"),
        "confidence": result_dict.get("confidence"),
        "patterns": len(result_dict.get("table_access_patterns") or []),
        "elapsed_sec": f"{time.monotonic() - started:.2f}",
    }
    if unavailable:
        log_warning(
            _STEP, "unavailable",
            "scan 모델 후보 미가용 — 검증되지 않은 패턴은 적용하지 않음",
            **fields,
        )
    else:
        log_info(_STEP, "done", **fields)
    return {"scan_result": result_dict}
