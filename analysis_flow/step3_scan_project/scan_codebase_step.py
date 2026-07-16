"""step3_scan_project — Framework 만 사용. Scan Agent 1회 invoke.

흐름:
1. ``collect_scanner_input`` — 매니페스트 + 확장자 빈도 + 카테고리별 파일 목록 수집.
2. ``run_scan_agent`` — Agent 호출 → ``ScanResult`` Pydantic.
3. ``state["scan_result"]`` 에 dict 로 저장 → step4_linking 가 사용.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.workflow.step_utils import step_config

from agent_tools.code_inspection import SKILLS_DIR
from analysis_flow.step3_scan_project.scan_agent import run_scan_agent
from analysis_flow.step3_scan_project.scanner_input_collector import (
    collect_scanner_input,
)
from shared.llm.cost_budget import CostBudget
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.log.logger import log_error, log_info, log_warning
from shared.workflow.graph_stream import emit_message

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
    _warn_if_call_wrappers_missing(result_dict)
    _remove_project_functions_from_framework_drop(
        result_dict, state.get("code_nodes", []),
    )
    emit_message(
        stream,
        f"✅ 코드베이스 스캔 완료 — 패턴 {len(result_dict.get('table_access_patterns') or [])}",
    )
    log_info(
        _STEP, "done",
        matched_skill=result_dict.get("matched_skill"),
        confidence=result_dict.get("confidence"),
        patterns=len(result_dict.get("table_access_patterns") or []),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"scan_result": result_dict}


def _warn_if_call_wrappers_missing(result_dict: dict) -> None:
    """matched_skill 의 SKILL contract 에 ``call_wrappers``가 있는데 Agent 가
    ``call_wrappers`` 를 빈 배열로 넘겼으면 표면화 — silent skip 차단.

    실측 사례: ``mpfm_dlcall("zapamcom10040", ...)`` 11 건이 호출 그래프에서 사라진
    사고 — SKILL 에 절은 있는데 Agent 가 ScanResult.call_wrappers 에 안 옮김 →
    wrapper_patterns 빈 dict → wrapper 호출이 framework drop 으로 빠짐.
    """
    matched = result_dict.get("matched_skill")
    if not matched or result_dict.get("call_wrappers"):
        return        # SKILL 매칭 없음, 또는 Agent 가 옮겼음 — 정상
    skill_path = SKILLS_DIR / matched / "SKILL.md"
    if not skill_path.is_file():
        return
    skill_text = skill_path.read_text(encoding="utf-8")
    if not re.search(r"(?m)^call_wrappers:\s*\n\s+-\s+wrapper:", skill_text):
        return        # SKILL 에 절 자체 없음 = 빈 배열이 정답
    log_warning(
        _STEP, "call_wrappers_missing_from_scan",
        f"SKILL '{matched}' contract에 call_wrappers가 있으나 ScanResult.call_wrappers "
        "빈 배열 — Agent 가 옮기지 않음. wrapper 호출이 framework drop 으로 빠져 "
        "호출 그래프에서 손실 (mpfm_dlcall 류 모듈 간 의존 누락).",
        skill=matched,
    )


def _remove_project_functions_from_framework_drop(
    result_dict: dict, code_nodes: list,
) -> None:
    """프로젝트 함수와 정확히 겹치는 framework drop 항목을 제거한다.

    실측 사례(2026-06-26): 스캔 에이전트가 ``framework_runtime_functions`` 에 프레임워크
    런타임 함수(PFM_*/mpfm_*) 대신 프로젝트 업무 함수(a000_*/b000_*)를 채움 → 자기 함수
    호출이 framework drop 으로 빠져 internal 호출 그래프가 붕괴(internal 54 같은 비정상).
    이 둘은 상호 배타다. 정확 이름 교집합만 제거하며 glob 의미는 추정하지 않는다.
    """
    drop = result_dict.get("framework_runtime_functions") or []
    if not drop or not code_nodes:
        return
    project_names: set[str] = set()
    for fn in code_nodes:
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        if name and str(name).strip():
            project_names.add(str(name).strip())
    # 글로브 메타문자가 있는 항목(PRINT_* 류)은 오탐 방지 위해 제외 — 정확 이름만 대조.
    overlap = sorted(
        d for d in drop
        if not any(c in d for c in "*?[") and d in project_names
    )
    if overlap:
        result_dict["framework_runtime_functions"] = [
            item for item in drop if item not in set(overlap)
        ]
        log_warning(
            _STEP, "framework_drop_project_functions_removed",
            f"framework_runtime_functions 에 프로젝트 자기 함수 {len(overlap)}건 혼입 "
            f"(예: {', '.join(overlap[:8])}) — 정확 교집합을 드롭 목록에서 제거해 "
            "프로젝트 호출 그래프를 보존했다.",
            overlap_count=len(overlap),
        )
