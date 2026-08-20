"""stage2_extract_calls — Framework 호출 결정론 분류 → ``Calls`` 엣지.

호출은 코드 목록 대조만으로 분류된다 (LLM·에이전트 0):
프로젝트 루틴 → internal / stdlib·프레임워크 → 드롭 / 그 외 → external.
inactive는 occurrence ledger에 보존하고 조건부·expression·owner·wrapper target 증거 부족은
``call_resolution_ledger``에 명시한다.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from pipeline.execution.step_config import step_config

from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
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
        return {"calls_edges": [], "call_resolution_ledger": []}

    started = time.monotonic()
    # framework 루틴 = drop 대상 (SKILL). call_wrappers는 argument가 동적 target인
    # 루틴이다. literal/symbol fact 전에는 target을 재파싱하지 않고 unresolved로 보존한다.
    scan_result = require_scan_result(state)
    framework_names = frozenset(scan_result.framework_runtime_functions)
    call_wrappers = scan_result.call_wrappers
    result = classify_call_sites(
        call_sites, state.get("code_nodes", []),
        framework_names=framework_names, wrappers=call_wrappers,
        macro_facts=state.get("macro_facts", []),
    )

    if neo is not None and result.edges:
        await upsert_calls(neo, result.edges)

    # 라이브 스트림 — 호출 관계를 step 완료 즉시 화면으로. external 은 stub 노드까지 함께
    # 발신(emit_calls = upsert_calls 와 동일 그래프) → 라이브=neo4j (외부호출 끝점 일치).
    stream = cfg.get("stream")
    await emit_calls(stream, result.edges)
    emit_message(stream, f"✅ 호출 이름 분류 완료 — {len(result.edges)}건")

    if result.wrapper_unresolved:
        log_warning(
            _STEP, "wrapper_unresolved",
            "wrapper target literal/symbol evidence 미지원 — source를 재파싱하지 않고 unresolved 보존",
            count=result.wrapper_unresolved,
        )
    if result.unresolved:
        log_warning(
            _STEP, "call_occurrences_unresolved",
            "canonical call occurrence 중 target/owner/presence가 확정되지 않은 항목",
            count=len(result.unresolved),
        )

    internal = sum(1 for e in result.edges if e.scope == "internal")
    log_info(
        _STEP, "done",
        call_sites=len(call_sites),
        internal=internal, external=len(result.edges) - internal,
        dropped_stdlib=result.dropped_stdlib,
        dropped_framework=result.dropped_framework,
        dropped_macro=result.dropped_macro,
        preserved_inactive=result.preserved_inactive,
        unresolved_presence=result.unresolved_presence,
        unresolved_expression=result.unresolved_expression,
        unresolved_owner=result.unresolved_owner,
        unresolved_macro=result.unresolved_macro,
        wrapper_unresolved=result.wrapper_unresolved or None,
        accounted=result.accounted,
        calls_edges=len(result.edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "calls_edges": result.edges,
        "call_resolution_ledger": result.ledger,
    }
