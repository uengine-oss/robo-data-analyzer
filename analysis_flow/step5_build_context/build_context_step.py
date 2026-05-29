"""step5_build_context — 모든 Function 에 ``AnalysisContext`` 생성.

흐름:
1. 함수 메타 + Linking 결과 (reads/writes/callers/callees) 로 결정적 컨텍스트 생성.
2. ``strategy.context_builder.augment()`` 호출 → strategy 별 ``extra`` 슬롯 채움.
3. 큰 함수 본문 토큰 > 임계 → ``large_function_one_liner`` LLM 1 회로 사전 압축.

summary 슬롯은 None 으로 둠 → ``step6_analyze_function`` 이 Bottom-up DAG 로 채움.

LLM 호출 = 큰 함수만 1 회 (작은 모델).

**성능**: 같은 file 을 토큰 추정 / one_liner 계산에서 두 번 read 하던 것을 1 회로
통합 — ``SourceFileCache`` 가 file_path → 텍스트 라인 캐싱.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from shared.source_file_cache import SourceFileCache
from shared.workflow.step_utils import step_config

from analysis_flow.step5_build_context.large_function_one_liner import (
    compute_one_liner,
    estimate_tokens,
)
from data_models.analysis_context import AnalysisContext
from data_models.graph_edges import References
from data_models.graph_nodes import Constant, Function, Variable
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from shared.llm.fanout import gather_with_warmup
from shared.log.logger import log_error, log_info, log_warning
from shared.log.processing_progress import step_processing_scope, track_processing

_STEP = "step5.build_context"


def _build_reference_context(state: dict) -> dict[str, list[str]]:
    """state 엣지/노드 → function_id 별 참조 심볼 줄.

    referenced_symbols = REFERENCES(함수→변수/상수) + INIT_BY(변수 초기화 출처).
    결정론 — LLM / Neo4j 조회 0. 같은 심볼은 함수당 1회만.
    """
    var_by_id: dict[str, Variable] = {v.variable_id: v for v in state.get("variables", [])}
    const_by_id: dict[str, Constant] = {c.constant_id: c for c in state.get("constants", [])}
    # InitBy 출발이 변수+상수 둘 다 가능 → (id, label) 합성키로 lookup.
    # 한 노드가 여러 출처 참조 시 dict 1:1 한계 (마지막만 표시) — 보조 정보용이라 허용.
    initby_by_target: dict[tuple[str, str], tuple[str, str]] = {
        (e.from_id, e.from_label): (e.source_id, e.source_label)
        for e in state.get("init_by_edges", [])
    }

    refsym_by_fn: dict[str, list[str]] = {}
    seen_sym: dict[str, set[str]] = {}
    for e in state.get("references_edges", []):
        if e.target_id in seen_sym.setdefault(e.function_id, set()):
            continue
        line = _format_referenced_symbol(e, var_by_id, const_by_id, initby_by_target)
        if line:
            seen_sym[e.function_id].add(e.target_id)
            refsym_by_fn.setdefault(e.function_id, []).append(line)

    return refsym_by_fn


def _format_referenced_symbol(
    ref: References,
    var_by_id: dict[str, Variable],
    const_by_id: dict[str, Constant],
    initby_by_target: dict[tuple[str, str], tuple[str, str]],
) -> str:
    """REFERENCES 엣지 1개 → 참조 심볼 한 줄. 노드 미발견 시 빈 문자열.

    변수·상수 모두 초기화 출처가 있으면 ``↳ 초기화 출처`` 로 부기.
    """
    if ref.target_label == "CONSTANT":
        c = const_by_id.get(ref.target_id)
        if c is None:
            return ""
        head = f"[상수] {c.name}"
        if c.var_type:
            head += f" ({c.var_type})"
        if c.value:
            head += f" = {c.value}"
    else:
        v = var_by_id.get(ref.target_id)
        if v is None:
            return ""
        head = f"[변수] {v.name}"
        if v.var_type:
            head += f" ({v.var_type})"
        if v.init_value:
            head += f" := {v.init_value}"

    src = initby_by_target.get((ref.target_id, ref.target_label))
    if src is not None:
        src_node = (const_by_id if src[1] == "CONSTANT" else var_by_id).get(src[0])
        if src_node is not None:
            head += f"  ↳ 초기화 출처: {src_node.name}"
    return head


async def build_context_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """모든 Function 에 ``AnalysisContext`` 생성."""
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget: CostBudget | None = cfg.get("cost_budget")
    stream = cfg.get("stream")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    started = time.monotonic()

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

    # 참조 컨텍스트 — REFERENCES + INIT_BY (참조 변수/상수).
    refsym_by_fn = _build_reference_context(state)

    # 파일 본문 캐시 — 같은 file 여러 함수가 공유 (성능: N*M read → N read).
    body_cache = SourceFileCache()

    # 컨텍스트 생성 (결정적 메타)
    contexts: dict[str, AnalysisContext] = {}
    for fn in functions:
        body_text = body_cache.slice(fn.file_path, fn.start_line, fn.end_line)
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
            referenced_symbols=tuple(refsym_by_fn.get(fn.function_id, ())),
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
        # 첫 함수 1건으로 prefix 캐시 워밍 → 나머지 병렬 (cold 동시요청 캐시 미스 방지).
        with step_processing_scope(_STEP, stream):
            results = await gather_with_warmup(tasks, return_exceptions=True)
        failed = 0
        for fn, r in zip(big_functions, results):
            if isinstance(r, BudgetExceededError):
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
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"analysis_contexts": contexts}


async def _compute_one_liner_safe(
    fn: Function,
    contexts: dict[str, AnalysisContext],
    cost_budget: Optional[CostBudget],
    semaphore: asyncio.Semaphore,
    body_cache: SourceFileCache,
) -> None:
    """본문 캐시 사용해서 one_liner 계산 → ``ctx.one_liner`` 채움.

    회로 차단기는 raise (gather 가 잡아 상위로 전파).
    track_processing 이 start/done/failed 자동 emit — stream 인자 chain 불필요.
    """
    async with track_processing("Function", fn.function_id, sub_action="one_liner"):
        async with semaphore:
            body_text = body_cache.slice(fn.file_path, fn.start_line, fn.end_line)
            one_liner = await compute_one_liner(fn, body_text, cost_budget=cost_budget)
            if one_liner:
                contexts[fn.function_id].one_liner = one_liner
