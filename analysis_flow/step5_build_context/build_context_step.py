"""step5_build_context — 모든 CodeNode 에 ``AnalysisContext`` 생성.

흐름:
1. 코드노드 메타 + Linking 결과 (reads/writes/callers/callees) 로 결정적 컨텍스트 생성.
2. ``strategy.context_builder.augment()`` 호출 → strategy 별 ``extra`` 슬롯 채움.
3. 큰 코드노드 본문 토큰 > 임계 → ``large_code_one_liner`` LLM 1 회로 사전 압축.

summary 슬롯은 None 으로 둠 → ``step6_analyze_code`` 이 Bottom-up DAG 로 채움.

LLM 호출 = 큰 코드노드만 1 회 (작은 모델).

**성능**: 같은 file 을 토큰 추정 / one_liner 계산에서 두 번 read 하던 것을 1 회로
통합 — ``SourceFileCache`` 가 file_path → 텍스트 라인 캐싱.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.source_file_cache import SourceFileCache
from shared.workflow.step_utils import step_config

from analysis_flow.step5_build_context.large_code_one_liner import (
    compute_one_liner,
    estimate_tokens,
)
from data_models.analysis_context import AnalysisContext
from data_models.graph_edges import References
from data_models.graph_nodes import Constant, CodeNode, Variable
from shared.config.env_catalog import ENV
from shared.llm.cost_budget import BudgetExceededError, CostBudget
from shared.llm.fanout import gather_with_warmup
from shared.log.logger import log_error, log_info, log_warning
from shared.log.processing_progress import step_processing_scope, track_processing

_STEP = "step5.build_context"


def _build_reference_context(state: dict) -> dict[str, list[str]]:
    """state 엣지/노드 → code_id 별 참조 심볼 줄.

    referenced_symbols = REFERENCES(코드노드→변수/상수) + INIT_BY(변수 초기화 출처).
    결정론 — LLM / Neo4j 조회 0. 같은 심볼은 코드노드당 1회만.
    """
    var_by_id: dict[str, Variable] = {v.id: v for v in state.get("variables", [])}
    const_by_id: dict[str, Constant] = {c.id: c for c in state.get("constants", [])}
    # InitBy 출발 id → 출처 id. 한 노드가 여러 출처 참조 시 dict 1:1 한계 (마지막만 표시)
    # — 보조 정보용이라 허용. 변수/상수 구분은 const_by_id/var_by_id 멤버십으로 파생.
    initby_by_target: dict[str, str] = {
        e.from_id: e.source_id
        for e in state.get("init_by_edges", [])
    }

    refsym_by_fn: dict[str, list[str]] = {}
    seen_sym: dict[str, set[str]] = {}
    for e in state.get("references_edges", []):
        if e.target_id in seen_sym.setdefault(e.source_id, set()):
            continue
        line = _format_referenced_symbol(e, var_by_id, const_by_id, initby_by_target)
        if line:
            seen_sym[e.source_id].add(e.target_id)
            refsym_by_fn.setdefault(e.source_id, []).append(line)

    return refsym_by_fn


def _format_referenced_symbol(
    ref: References,
    var_by_id: dict[str, Variable],
    const_by_id: dict[str, Constant],
    initby_by_target: dict[str, str],
) -> str:
    """REFERENCES 엣지 1개 → 참조 심볼 한 줄. 노드 미발견 시 빈 문자열.

    변수/상수 구분 = const_by_id 멤버십. 초기화 출처가 있으면 ``↳ 초기화 출처`` 로 부기.
    """
    c = const_by_id.get(ref.target_id)
    if c is not None:
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

    src_id = initby_by_target.get(ref.target_id)
    if src_id is not None:
        src_node = const_by_id.get(src_id) or var_by_id.get(src_id)
        if src_node is not None:
            head += f"  ↳ 초기화 출처: {src_node.name}"
    return head


async def build_context_step(state: dict, config: Optional[RunnableConfig] = None) -> dict:
    """모든 CodeNode 에 ``AnalysisContext`` 생성."""
    cfg = step_config(config)
    strategy = cfg.get("strategy")
    cost_budget: CostBudget | None = cfg.get("cost_budget")
    stream = cfg.get("stream")
    if strategy is None:
        log_warning(_STEP, "config_missing", "strategy 누락 — step skip")
        return {}

    started = time.monotonic()

    builder = strategy.context_builder
    code_nodes: list[CodeNode] = state.get("code_nodes", [])
    rw_edges = state.get("reads_writes_edges", [])
    calls_edges = state.get("calls_edges", [])
    log_info(
        _STEP, "start",
        code_nodes=len(code_nodes), reads_writes_edges=len(rw_edges), calls_edges=len(calls_edges),
        builder=type(builder).__name__,
    )

    # code_id → reads/writes/callers/callees 집계 (sorted tuple 강제 — 캐시 친화)
    reads_by_fn: dict[str, set[str]] = {}
    writes_by_fn: dict[str, set[str]] = {}
    for e in rw_edges:
        bucket = reads_by_fn if e.relation == "READS" else writes_by_fn
        bucket.setdefault(e.source_id, set()).add(e.target_id)

    callers_by_fn: dict[str, set[str]] = {}
    callees_by_fn: dict[str, set[str]] = {}
    for e in calls_edges:
        callees_by_fn.setdefault(e.caller_id, set()).add(e.callee_id)
        callers_by_fn.setdefault(e.callee_id, set()).add(e.caller_id)

    # 참조 컨텍스트 — REFERENCES + INIT_BY (참조 변수/상수).
    refsym_by_fn = _build_reference_context(state)

    # 파일 본문 캐시 — 같은 file 여러 코드노드가 공유 (성능: N*M read → N read).
    body_cache = cfg.get("source_cache") or SourceFileCache()   # run 스코프 공유 (FR-008)

    # 컨텍스트 생성 (결정적 메타)
    contexts: dict[str, AnalysisContext] = {}
    for fn in code_nodes:
        # 원문은 step2 가 이미 채웠다 (code_text) — 같은 (file,start,end) 를 여기서 다시
        # 잘라내지 않는다 (spec 046 FR-007: 슬라이싱 계산은 한 곳). 없을 때만 캐시에서 슬라이스
        # (code_text 가 빈 경우 = 소스 read 실패 — FR-004 가 이미 경고를 남겼다).
        body_text = fn.code_text or body_cache.slice(fn.file_path, fn.start_line, fn.end_line)
        ctx = AnalysisContext(
            id=fn.id,
            signature=fn.signature,
            file_path=fn.file_path,
            start_line=fn.start_line,
            end_line=fn.end_line,
            reads=tuple(sorted(reads_by_fn.get(fn.id, ()))),
            writes=tuple(sorted(writes_by_fn.get(fn.id, ()))),
            callers=tuple(sorted(callers_by_fn.get(fn.id, ()))),
            callees=tuple(sorted(callees_by_fn.get(fn.id, ()))),
            referenced_symbols=tuple(sorted(refsym_by_fn.get(fn.id, ()))),
            token_count=estimate_tokens(body_text),
        )
        ctx = await builder.augment(ctx, fn, state)
        contexts[fn.id] = ctx

    # 큰 코드노드만 one_liner 사전 압축 (LLM 1 회 — model_small)
    big_code_nodes = [
        fn for fn in code_nodes
        if contexts[fn.id].token_count >= ENV.pipeline.big_context_threshold_tokens
    ]
    if big_code_nodes:
        log_info(
            _STEP, "one_liner_start",
            big_code_nodes=len(big_code_nodes),
            threshold_tokens=ENV.pipeline.big_context_threshold_tokens,
            concurrency=ENV.pipeline.max_concurrency,
        )
        semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
        tasks = [
            _compute_one_liner_safe(fn, contexts, cost_budget, semaphore, body_cache)
            for fn in big_code_nodes
        ]
        # 첫 코드노드 1건으로 prefix 캐시 워밍 → 나머지 병렬 (cold 동시요청 캐시 미스 방지).
        with step_processing_scope(_STEP, stream):
            results = await gather_with_warmup(tasks, return_exceptions=True)
        failed = 0
        for fn, r in zip(big_code_nodes, results):
            if isinstance(r, BudgetExceededError):
                raise r        # 회로 차단기 즉시 전파
            if isinstance(r, BaseException):
                failed += 1
                log_error(
                    _STEP, "compute_one_liner", r,
                    code_id=fn.id,
                )
        log_info(_STEP, "one_liner_done", big_code_nodes=len(big_code_nodes), failed=failed or None)

    log_info(
        _STEP, "done",
        contexts=len(contexts), big_code_nodes=len(big_code_nodes),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {"analysis_contexts": contexts}


async def _compute_one_liner_safe(
    fn: CodeNode,
    contexts: dict[str, AnalysisContext],
    cost_budget: Optional[CostBudget],
    semaphore: asyncio.Semaphore,
    body_cache: SourceFileCache,
) -> None:
    """본문 캐시 사용해서 one_liner 계산 → ``ctx.one_liner`` 채움.

    회로 차단기는 raise (gather 가 잡아 상위로 전파).
    track_processing 이 start/done/failed 자동 emit — stream 인자 chain 불필요.
    """
    async with track_processing("CodeNode", fn.id, sub_action="one_liner"):
        async with semaphore:
            body_text = body_cache.slice(fn.file_path, fn.start_line, fn.end_line)
            one_liner = await compute_one_liner(fn, body_text, cost_budget=cost_budget)
            if one_liner:
                contexts[fn.id].one_liner = one_liner
