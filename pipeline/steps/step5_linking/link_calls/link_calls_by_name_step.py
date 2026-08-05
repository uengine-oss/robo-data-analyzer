"""stage2_extract_calls — Framework 호출 결정론 분류 → ``Calls`` 엣지.

호출은 코드 목록 대조만으로 분류된다 (LLM·에이전트 0):
프로젝트 루틴 → internal / stdlib·프레임워크 → 드롭 / 그 외 → external.
"미해결" 개념 없음 — ``unresolved_cases`` 에 아무것도 안 넣는다.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
from pipeline.languages import statement_labels
from pipeline.steps.step4_scan_project.scan_result_contract import require_scan_result
from shared.events.runtime_logger import log_info, log_warning
from graph.edges.calls import upsert_calls, emit_calls
from graph.neo4j_client import Neo4jClient
from pipeline.execution.graph_delta_stream import emit_message

_STEP = "step5.link_calls_by_name"


async def link_calls_by_name_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """call_sites → 결정론 분류 → ``Calls`` 엣지 (Framework). LLM 0회."""
    cfg = step_config(config)
    neo: Neo4jClient | None = cfg.get("neo_client")

    # call site 는 step2(load_ast) 가 antlr JSON 에서 수집한 것을 state 로 받는다.
    call_sites = state.get("call_sites", [])
    if not call_sites:
        log_info(_STEP, "done", call_sites=0, calls_edges=0, note="no call sites")
        return {"calls_edges": []}

    started = time.monotonic()
    # framework 루틴 = drop 대상 (SKILL). call_wrappers = 인자가 실제 호출 대상인
    # 루틴 (SKILL) — call_classifier 가 라인 본문에서 인자 추출 후 internal/external 분류.
    scan_result = require_scan_result(state)
    framework_names = frozenset(scan_result.framework_runtime_functions)
    call_wrappers = scan_result.call_wrappers
    result = classify_call_sites(
        call_sites, state.get("code_nodes", []),
        framework_names=framework_names, wrappers=call_wrappers,
        # run 스코프 공유 캐시 (executor 주입) — wrapper 인자 추출이 소스를 읽는다 (FR-008).
        source_cache=cfg.get("source_cache"),
    )

    # external LLM 재검증 (spec 113) — internal 은 절대 불가침, 게이트 통과 드롭만 반영.
    result.edges = await _maybe_review_externals(
        result.edges, state.get("code_nodes", []), cfg,
    )

    if neo is not None and result.edges:
        await upsert_calls(neo, result.edges)

    # 라이브 스트림 — 호출 관계를 step 완료 즉시 화면으로. external 은 stub 노드까지 함께
    # 발신(emit_calls = upsert_calls 와 동일 그래프) → 라이브=neo4j (외부호출 끝점 일치).
    stream = cfg.get("stream")
    await emit_calls(stream, result.edges)
    emit_message(stream, f"✅ 호출 이름 분류 완료 — {len(result.edges)}건")

    # call_text / caller 누락 = AST 불완전 — 드롭하되 조용히 묻지 않는다.
    if result.dropped_incomplete:
        log_warning(
            _STEP, "incomplete_call_sites",
            "call_text / caller 누락 call site — 분류 불가, 드롭",
            count=result.dropped_incomplete,
        )
    if result.wrapper_unresolved:
        log_warning(
            _STEP, "wrapper_unresolved",
            "wrapper 매치했으나 본문에서 인자 추출 실패 (라인/정규식 불일치 가능)",
            count=result.wrapper_unresolved,
        )

    internal = sum(1 for e in result.edges if e.scope == "internal")
    log_info(
        _STEP, "done",
        call_sites=len(call_sites),
        internal=internal, external=len(result.edges) - internal,
        dropped_stdlib=result.dropped_stdlib,
        dropped_framework=result.dropped_framework,
        dropped_macro=result.dropped_macro,
        dropped_incomplete=result.dropped_incomplete,
        wrapper_unresolved=result.wrapper_unresolved or None,
        calls_edges=len(result.edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"calls_edges": result.edges}


async def _maybe_review_externals(edges: list, code_nodes: list, cfg: dict) -> list:
    """external 콜리 LLM 재검증. 모델 출력 실패면 원 결정론 엣지를 보존한다."""
    from collections import Counter

    from shared.config.settings import SETTINGS

    if not SETTINGS.analysis_agents.call_review_enabled:
        return edges
    run = cfg.get("run")
    cost_budget = cfg.get("cost_budget")
    if run is None or cost_budget is None:
        raise RuntimeError("external call review requires run and cost_budget")
    external_counts = Counter(e.callee_id for e in edges if e.scope == "external")
    if not external_counts:
        return edges

    from agents.call_review import review_external_calls
    from agents.runtime import AgentModelOutputError
    from pipeline.execution.quality_finding_log import record_analysis_findings
    from pipeline.models.analysis_finding import AnalysisFinding

    # 루틴 보호 목록 — statement 노드(GOTO 라벨 등)의 name 은 루틴이 아니므로 제외
    # (포함 시 라벨과 같은 이름의 정당한 external drop 이 거절된다).
    stmt_labels = statement_labels()
    defined = frozenset(
        (fn.name or "").strip() for fn in code_nodes
        if (fn.name or "").strip() and fn.label not in stmt_labels
    )
    stream = cfg.get("stream")
    try:
        drops = await review_external_calls(
            dict(external_counts), defined, run.run_dir, cost_budget,
            stream_emit=stream.emit if stream is not None else None,
        )
    except AgentModelOutputError as error:
        record_analysis_findings([AnalysisFinding(
            concern="external_call_review",
            status="unavailable",
            target_id="external_calls",
            reasons=(f"{type(error).__name__}: {error}",),
            evidence_rule="call_review_agent",
        )])
        log_info(
            _STEP,
            "call_review_unavailable",
            preserved_edges=len(edges),
            reason=f"{type(error).__name__}: {error}",
        )
        return edges
    if not drops:
        return edges
    kept = [e for e in edges if not (e.scope == "external" and e.callee_id in drops)]
    log_info(_STEP, "call_review_applied",
             dropped_edges=len(edges) - len(kept), dropped_names=len(drops))
    return kept
