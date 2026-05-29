"""stage1_regex_grep — ``ScanResult.table_access_patterns`` 로 코드 grep → R/W 엣지.

LLM 0회 / Agent 0회. 매칭 안 된 케이스는 ``unresolved_cases`` 큐에 누적
(stage3_residual_patterns 의 잔여 패턴 루프가 처리, Framework 전용).

정규식은 step3 Scan 이 **검증을 통과시킨** 것만 넘어온다 — 그래도 패턴이 있는데
엣지 0건이면 정규식이 소스와 안 맞는 것이므로 ``log_warning`` 으로 표면화한다.
"""
from __future__ import annotations

import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from data_models.agent_output import ScanResult
from analysis_flow.step4_linking.stage1_regex_grep.regex_table_linker import RegexTableLinker
from shared.log.logger import log_info, log_warning
from shared.neo4j.edges.reads_writes import upsert_reads_writes
from shared.neo4j.neo4j_client import Neo4jClient

_STEP = "step4.link_deterministic"


async def link_deterministic_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """결정론 grep → ``ReadsWrites`` 엣지 + ``unresolved`` 큐."""
    cfg = step_config(config)
    project = cfg.get("codebase")
    neo: Neo4jClient | None = cfg.get("neo_client")
    if project is None:
        log_warning(_STEP, "config_missing", "project 누락 — step skip")
        return {}

    scan = _load_scan(state)
    if scan is None:
        log_info(_STEP, "skip", reason="scan_result 없음/검증 실패")
        return {"reads_writes_edges": [], "unresolved_cases": []}

    functions_in = state.get("functions") or []
    tables_in = state.get("tables") or []
    patterns = scan.table_access_patterns
    started = time.monotonic()
    log_info(
        _STEP, "start",
        root=str(project.root),
        patterns=len(patterns), functions=len(functions_in), tables=len(tables_in),
    )

    outcome = await RegexTableLinker().link(
        project_root=project.root,
        functions=functions_in,
        tables=tables_in,
        patterns=patterns,
        ddl_transform=scan.ddl_transform,
    )

    # raw 중복 (같은 함수·테이블·동작을 여러 라인에서 매칭) 은 Neo4j MERGE 가 자동
    # dedup — state 의 list 는 raw 그대로. 다른 step 이 set 으로 변환해 사용.
    if neo is not None and outcome.edges:
        await upsert_reads_writes(neo, outcome.edges)

    # 패턴은 있는데 R/W 엣지 0 — 정규식이 소스와 안 맞음 (조용한 실패 표면화).
    if patterns and not outcome.edges:
        log_warning(
            _STEP, "no_edges",
            "table_access_patterns 가 있는데 R/W 엣지 0건 — 정규식이 소스와 안 맞음. "
            "step3 Scan 검증을 통과한 패턴인지 확인 필요",
            patterns=len(patterns), matched_sites=outcome.matched_sites,
        )

    log_info(
        _STEP, "done",
        reads_writes_edges=len(outcome.edges),
        unresolved_cases=len(outcome.unresolved),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "reads_writes_edges": outcome.edges,
        "unresolved_cases": outcome.unresolved,
    }


def _load_scan(state: dict) -> Optional[ScanResult]:
    """``state['scan_result']`` (dict) → ``ScanResult``. 없거나 파싱 실패면 None.

    ScanResult 검증을 여기서 — RegexTableLinker 는 타입 보장된 입력만 받는다.
    """
    raw = state.get("scan_result")
    if not raw:
        return None
    try:
        return ScanResult.model_validate(raw)
    except Exception as e:  # noqa: BLE001 — 검증 실패 표면화 목적
        log_warning(
            _STEP, "validate_scan",
            "scan_result 파싱 실패 — 결정론 링킹 건너뜀 (전체 unresolved 처리됨)",
            error=f"{type(e).__name__}: {e}",
        )
        return None
