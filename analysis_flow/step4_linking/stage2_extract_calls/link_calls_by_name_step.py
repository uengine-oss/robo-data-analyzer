"""stage2_extract_calls — Framework 호출 결정론 분류 → ``Calls`` 엣지.

호출은 코드 목록 대조만으로 분류된다 (LLM·에이전트 0):
프로젝트 함수 → internal / stdlib·프레임워크 → 드롭 / 그 외 → external.
"미해결" 개념 없음 — ``unresolved_cases`` 에 아무것도 안 넣는다.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow_runner.step_config import step_config

from shared.lang.call_classifier import classify_call_sites
from shared.logging_and_stream.logger import log_info, log_warning
from shared.neo4j.edges.calls import upsert_calls
from shared.neo4j.neo4j_client import Neo4jClient

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
    # 프레임워크 함수 목록 = step3 scan 이 skill 문서에서 읽어 채운 것.
    # 없으면 빈 집합 → framework 드롭 없음 (프레임워크 호출은 external 로 남음).
    scan_result = state.get("scan_result") or {}
    framework_names = frozenset(scan_result.get("framework_functions") or [])
    result = classify_call_sites(call_sites, state.get("functions", []), framework_names)

    if neo is not None and result.edges:
        await upsert_calls(neo, result.edges)

    # call_text / caller 누락 = AST 불완전 — 드롭하되 조용히 묻지 않는다.
    if result.dropped_incomplete:
        log_warning(
            _STEP, "incomplete_call_sites",
            "call_text / caller 누락 call site — 분류 불가, 드롭",
            count=result.dropped_incomplete,
        )

    internal = sum(1 for e in result.edges if e.scope == "internal")
    log_info(
        _STEP, "done",
        call_sites=len(call_sites),
        internal=internal, external=len(result.edges) - internal,
        dropped_stdlib=result.dropped_stdlib,
        dropped_framework=result.dropped_framework,
        dropped_incomplete=result.dropped_incomplete,
        calls_edges=len(result.edges),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"calls_edges": result.edges}
