"""step5 stage3 — 잔여 패턴 루프 step (Framework 전용).

결정론 R/W 링킹(stage1)이 못 푼 ``unresolved_cases`` 를 받아 — 잔여 패턴 루프로
추가 ``ReadsWrites`` 엣지를 만들고, 끝내 못 푼 잔여만 ``unresolved_cases`` 로
되돌린다 (정직한 미해결 기록 — 다운스트림 artifact 가 이 최종값을 덤프).

옛 per-case Linking Agent(``link_unresolved_step``)의 후신. 에이전트를 케이스마다
부르지 않고, 패턴을 찾아 코드가 일괄 적용한다 — 루프 본체는 ``residual_pattern_loop``.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from agents.residual import (
    ResidualOutcome,
    ResidualPatternVerificationError,
    run_residual_loop,
)
from agents.runtime import AgentModelOutputError
from pipeline.execution.quality_finding_log import record_analysis_findings
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.steps.step4_scan_project.scan_result_contract import require_scan_result
from shared.events.runtime_logger import log_info
from graph.edges.reads_writes import upsert_reads_writes
from graph.neo4j_client import Neo4jClient
from pipeline.execution.graph_delta_stream import emit_edges, emit_message

_STEP = "step5.link_residual_patterns"


async def link_residual_patterns_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """잔여 미해결 케이스 → 패턴 루프 → 추가 ``ReadsWrites`` 엣지 + 최종 잔여."""
    cfg = step_config(config)
    project = cfg.get("codebase")
    cost_budget = cfg.get("cost_budget")
    stream = cfg.get("stream")
    neo: Neo4jClient | None = cfg.get("neo_client")

    unresolved = state.get("unresolved_cases") or []
    if not unresolved:
        log_info(_STEP, "skip", reason="잔여 미해결 케이스 없음")
        return {}
    if project is None or cost_budget is None:
        raise RuntimeError(
            "link_residual_patterns requires project and cost_budget"
        )
    scan_result = require_scan_result(state)

    started = time.monotonic()
    log_info(_STEP, "start", cases=len(unresolved))

    try:
        outcome = await run_residual_loop(
            unresolved_cases=unresolved,
            scan_result=scan_result,
            project_root=project.root,
            code_nodes=state.get("code_nodes") or [],
            tables=state.get("tables") or [],
            cost_budget=cost_budget,
        )
    except (AgentModelOutputError, ResidualPatternVerificationError) as error:
        status = "unavailable" if isinstance(error, AgentModelOutputError) else "rejected"
        record_analysis_findings([AnalysisFinding(
            concern="residual_table_linking",
            status=status,
            target_id="unresolved_cases",
            reasons=(f"{type(error).__name__}: {error}",),
            evidence_rule="residual_agent",
        )])
        log_info(
            _STEP,
            "candidate_quarantined",
            status=status,
            residual=len(unresolved),
            reason=f"{type(error).__name__}: {error}",
        )
        outcome = ResidualOutcome(edges=[], residual=list(unresolved))

    if neo is not None and outcome.edges:
        await upsert_reads_writes(neo, outcome.edges)

    # 라이브 스트림 — 잔여 패턴 R/W 링킹 결과를 step 완료 즉시 화면으로.
    await emit_edges(stream, outcome.edges)
    emit_message(stream, f"✅ 잔여 패턴 링킹 완료 — {len(outcome.edges)}건")

    log_info(
        _STEP, "done",
        cases=len(unresolved),
        resolved=len(unresolved) - len(outcome.residual),
        still_unresolved=len(outcome.residual),
        rounds=outcome.rounds_used,
        patterns_found=outcome.patterns_found,
        reads_writes_edges=len(outcome.edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    # unresolved_cases 는 평키(reducer 없음) — 결정론 단계가 쓴 raw 를 최종 잔여로 덮는다.
    return {
        "reads_writes_edges": outcome.edges,
        "unresolved_cases": outcome.residual,
    }
