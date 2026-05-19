"""step5_build_context — 모든 Function 에 ``AnalysisContext`` 생성.

흐름:
1. 함수 메타 + Linking 결과 (reads/writes/callers/callees) 로 결정적 컨텍스트 생성.
2. ``strategy.context_builder.augment()`` 호출 → strategy 별 ``extra`` 슬롯 채움.
3. 큰 함수 본문 토큰 > 임계 → ``large_function_one_liner`` LLM 1 회로 사전 압축.

summary 슬롯은 None 으로 둠 → ``step6_analyze_function`` 이 Bottom-up DAG 로 채움.

LLM 호출 = 큰 함수만 1 회 (작은 모델).

**성능**: 같은 file 을 토큰 추정 / one_liner 계산에서 두 번 read 하던 것을 1 회로
통합 — ``_FileBodyCache`` 가 file_path → body text 캐싱.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from analysis_flow.step5_build_context.large_function_one_liner import (
    compute_one_liner,
    estimate_tokens,
)
from data_models.analysis_context import AnalysisContext
from data_models.graph_nodes import Function
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from shared.llm.middleware.call_limit import ModelCallLimitExceeded
from shared.logging_and_stream.logger import log_error, log_info, log_warning

_STEP = "step5.build_context"


class _FileBodyCache:
    """file_path → 전체 텍스트 캐시. 같은 파일 여러 함수가 공유.

    크기 제한 없음 — 분석 끝나면 step 종료와 함께 GC. 매우 큰 모노레포에서만
    메모리 우려 (현재 운영 대상 8 프로젝트 기준 무시 가능).
    """

    def __init__(self):
        self._cache: dict[str, Optional[str]] = {}

    def get_body(self, file_path: str) -> str:
        """파일 텍스트 — 캐시 적중 시 즉시. 실패 시 빈 문자열 (1회만 시도)."""
        if file_path in self._cache:
            cached = self._cache[file_path]
            return cached or ""
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            self._cache[file_path] = text
            return text
        except (OSError, UnicodeDecodeError):
            self._cache[file_path] = None
            return ""

    def slice_body(self, file_path: str, start_line: int, end_line: int) -> str:
        """함수의 라인 범위 본문."""
        text = self.get_body(file_path)
        if not text:
            return ""
        lines = text.splitlines()
        return "\n".join(lines[max(start_line - 1, 0) : end_line])


async def build_context_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """모든 Function 에 ``AnalysisContext`` 생성."""
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    strategy = cfg.get("strategy")
    cost_budget: CostBudget | None = cfg.get("cost_budget")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    import time as _time
    started = _time.monotonic()

    builder = strategy.context_builder
    functions: list[Function] = state.get("functions", [])
    rw_edges = state.get("reads_writes_edges", [])
    calls_edges = state.get("calls_edges", [])
    log_info(
        _STEP, "start",
        functions=len(functions), reads_writes_edges=len(rw_edges), calls_edges=len(calls_edges),
        builder=type(builder).__name__,
    )

    # function_id → reads/writes/callers/callees 집계 (sorted tuple 강제 — 캐시 친화)
    reads_by_fn: dict[str, set[str]] = {}
    writes_by_fn: dict[str, set[str]] = {}
    for e in rw_edges:
        bucket = reads_by_fn if e.relation == "READS" else writes_by_fn
        bucket.setdefault(e.function_id, set()).add(e.table_fqn)

    callers_by_fn: dict[str, set[str]] = {}
    callees_by_fn: dict[str, set[str]] = {}
    for e in calls_edges:
        callees_by_fn.setdefault(e.caller_function_id, set()).add(e.callee_function_id)
        callers_by_fn.setdefault(e.callee_function_id, set()).add(e.caller_function_id)

    # 파일 본문 캐시 — 같은 file 여러 함수가 공유 (성능: N*M read → N read).
    body_cache = _FileBodyCache()

    # 컨텍스트 생성 (결정적 메타)
    contexts: dict[str, AnalysisContext] = {}
    for fn in functions:
        body_text = body_cache.slice_body(fn.file_path, fn.start_line, fn.end_line)
        ctx = AnalysisContext(
            function_id=fn.function_id,
            signature=fn.signature,
            file_path=fn.file_path,
            start_line=fn.start_line,
            end_line=fn.end_line,
            reads=tuple(sorted(reads_by_fn.get(fn.function_id, ()))),
            writes=tuple(sorted(writes_by_fn.get(fn.function_id, ()))),
            callers=tuple(sorted(callers_by_fn.get(fn.function_id, ()))),
            callees=tuple(sorted(callees_by_fn.get(fn.function_id, ()))),
            token_count=estimate_tokens(body_text),
        )
        ctx = await builder.augment(ctx, fn, state)
        contexts[fn.function_id] = ctx

    # 큰 함수만 one_liner 사전 압축 (LLM 1 회 — model_small)
    big_functions = [
        fn for fn in functions
        if contexts[fn.function_id].token_count >= ENV.pipeline.big_context_threshold_tokens
    ]
    if big_functions:
        log_info(
            _STEP, "one_liner_start",
            big_functions=len(big_functions),
            threshold_tokens=ENV.pipeline.big_context_threshold_tokens,
            concurrency=ENV.pipeline.max_concurrency,
        )
        semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
        tasks = [
            _compute_one_liner_safe(fn, contexts, cost_budget, semaphore, body_cache)
            for fn in big_functions
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failed = 0
        for fn, r in zip(big_functions, results):
            if isinstance(r, (BudgetExceededError, ModelCallLimitExceeded)):
                raise r        # 회로 차단기 즉시 전파
            if isinstance(r, BaseException):
                failed += 1
                log_error(
                    _STEP, "compute_one_liner", r,
                    function_id=fn.function_id,
                )
        log_info(_STEP, "one_liner_done", big_functions=len(big_functions), failed=failed or None)

    log_info(
        _STEP, "done",
        contexts=len(contexts), big_functions=len(big_functions),
        elapsed_sec=f"{_time.monotonic() - started:.2f}",
    )
    return {"analysis_contexts": contexts}


async def _compute_one_liner_safe(
    fn: Function,
    contexts: dict[str, AnalysisContext],
    cost_budget: Optional[CostBudget],
    semaphore: asyncio.Semaphore,
    body_cache: _FileBodyCache,
) -> None:
    """본문 캐시 사용해서 one_liner 계산 → ``ctx.one_liner`` 채움.

    회로 차단기는 raise (gather 가 잡아 상위로 전파).
    """
    async with semaphore:
        body_text = body_cache.slice_body(fn.file_path, fn.start_line, fn.end_line)
        one_liner = await compute_one_liner(fn, body_text, cost_budget=cost_budget)
        if one_liner:
            contexts[fn.function_id].one_liner = one_liner
