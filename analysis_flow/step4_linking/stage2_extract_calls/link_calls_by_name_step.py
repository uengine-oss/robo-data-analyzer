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

from shared.lang.call_classifier import classify_call_sites
from shared.log.logger import log_info, log_warning
from graph.edges.calls import upsert_calls, emit_calls
from graph.neo4j_client import Neo4jClient
from pipeline.execution.graph_delta_stream import emit_message

_STEP = "step4.link_calls_by_name"


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
    scan_result = state.get("scan_result") or {}
    framework_names = frozenset(scan_result.get("framework_runtime_functions") or [])
    call_wrappers = scan_result.get("call_wrappers") or []
    result = classify_call_sites(
        call_sites, state.get("code_nodes", []),
        framework_names=framework_names, wrappers=call_wrappers,
        # run 스코프 공유 캐시 (executor 주입) — wrapper 인자 추출이 소스를 읽는다 (FR-008).
        source_cache=cfg.get("source_cache"),
    )

    if neo is not None and result.edges:
        await upsert_calls(neo, result.edges)

    # 라이브 스트림 — 호출 관계를 step 완료 즉시 화면으로. external 은 stub 노드까지 함께
    # 발신(emit_calls = upsert_calls 와 동일 그래프) → 라이브=neo4j (외부호출 끝점 일치).
    stream = cfg.get("stream")
    emit_calls(stream, result.edges)
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
        dropped_incomplete=result.dropped_incomplete,
        wrapper_unresolved=result.wrapper_unresolved or None,
        calls_edges=len(result.edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"calls_edges": result.edges}
