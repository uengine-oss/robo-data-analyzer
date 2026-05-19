"""step6_analyze_function — Bottom-up DAG + 3 분할 fan-out.

순서:
1. 호출 그래프 (CALLS + PARENT_OF) → ``find_call_cycles`` → cycle 식별 (Tarjan SCC).
2. ``bottom_up_rounds`` → leaf → root Round 별 정렬.
3. Round 별 병렬 분석:
   - 같은 Round 함수들 ``asyncio.gather`` + ``Semaphore`` 병렬.
   - Round 간 순차 — 다음 Round 의 caller 가 이전 Round 의 callee summary 사용.
4. 각 함수 = ``analyze_function_three_split`` (3 분할 fan-out, RadixAttention 95%+).

cycle 내부 함수 → callee summary 없이 분석 (``is_in_cycle=True`` 메타).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step6_analyze_function.analysis_order import bottom_up_rounds
from analysis_flow.step6_analyze_function.call_cycles import find_call_cycles
from analysis_flow.step6_analyze_function.three_split_llm_fanout import (
    analyze_function_three_split,
)
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.logging_and_stream.logger import log_error, log_info, log_warning
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.nodes.function import update_summaries

_STEP = "step6.analyze_function"


async def analyze_function_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """Bottom-up DAG + 3 분할 fan-out."""
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    strategy = cfg.get("strategy")
    cost_budget = cfg.get("cost_budget")
    neo: Neo4jClient | None = cfg.get("neo_client")

    contexts = state.get("analysis_contexts", {})
    functions_list = state.get("functions", [])
    function_by_id = {f.function_id: f for f in functions_list}

    # ProfileResult.codebase_notes — 3분할 모든 호출이 공통 prefix 로 사용 (캐시 친화).
    # Framework 만 채움 — DBMS 는 빈 문자열 (Profile Agent 안 씀).
    profile_result = state.get("profile_result") or {}
    codebase_notes = profile_result.get("codebase_notes", "")

    if not contexts:
        log_info(_STEP, "skip", reason="no analysis_contexts")
        return {}

    started = time.monotonic()

    # 1. cycle 식별 (CALLS + PARENT_OF 둘 다)
    edges: list[tuple[str, str]] = [
        (e.caller_function_id, e.callee_function_id)
        for e in state.get("calls_edges", [])
    ]
    edges.extend(
        (e.parent_function_id, e.child_function_id)
        for e in state.get("parent_of_edges", [])
    )
    all_ids = list(contexts.keys())
    cycles = find_call_cycles(edges, all_ids)

    # cycle 메타 ctx 에 부착
    for fn_id in cycles:
        if fn_id in contexts:
            contexts[fn_id].is_in_cycle = True

    # 2. Bottom-up Round 정렬
    rounds = bottom_up_rounds(edges, all_ids, cycles)
    log_info(
        _STEP, "start",
        functions=len(contexts), rounds=len(rounds), in_cycle=len(cycles),
        concurrency=ENV.pipeline.max_concurrency,
    )

    # 3. Round 별 병렬 분석 — Round 간은 순차
    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    analyzed: list = []
    total_failed = 0

    for round_idx, round_fn_ids in enumerate(rounds, start=1):
        round_started = time.monotonic()
        log_info(_STEP, "round_start", round=f"{round_idx}/{len(rounds)}", functions=len(round_fn_ids))
        tasks = [
            _analyze_one(fn_id, contexts, function_by_id, strategy, cost_budget, semaphore, codebase_notes)
            for fn_id in round_fn_ids
        ]
        # return_exceptions=True 로 받지만 회로 차단기는 즉시 전파.
        # 개별 분석 실패 (Pydantic 파싱 등) 만 로깅 후 건너뜀.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        round_failed = 0
        for fn_id, r in zip(round_fn_ids, results):
            if isinstance(r, (BudgetExceededError, ModelCallLimitExceeded)):
                raise r          # 절대원칙 #3 — 회로 차단기 즉시 전파
            if isinstance(r, BaseException):
                round_failed += 1
                log_error(_STEP, "round_analysis", r, function_id=fn_id, round=round_idx)
                continue
            if r is not None:
                analyzed.append(r)
        total_failed += round_failed
        log_info(
            _STEP, "round_done",
            round=f"{round_idx}/{len(rounds)}",
            analyzed=len(round_fn_ids) - round_failed, failed=round_failed or None,
            elapsed_sec=f"{time.monotonic() - round_started:.2f}",
        )

    # Neo4j 업서트 — Function.summary / stereotype / actor / logical_name + 3 분할 extra
    if neo is not None and analyzed:
        await update_summaries(neo, analyzed)

    log_info(
        _STEP, "done",
        functions=len(contexts), analyzed=len(analyzed),
        failed=total_failed or None, in_cycle=len(cycles),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"analysis_contexts": contexts}


async def _analyze_one(
    fn_id: str,
    contexts: dict,
    function_by_id: dict,
    strategy: Any,
    cost_budget,
    semaphore: asyncio.Semaphore,
    codebase_notes: str,
):
    async with semaphore:
        return await analyze_function_three_split(
            fn_id, contexts, function_by_id, strategy,
            cost_budget=cost_budget,
            codebase_notes=codebase_notes,
        )
