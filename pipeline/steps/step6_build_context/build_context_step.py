"""step6_build_context — 모든 CodeNode 에 ``AnalysisContext`` 생성.

흐름:
1. 코드노드 메타 + Linking 결과 (reads/writes/callers/callees) 로 결정적 컨텍스트 생성.
2. ``strategy.context_builder.augment()`` 호출 → strategy 별 ``extra`` 슬롯 채움.
3. 큰 코드노드 본문 토큰 > 임계 → ``large_code_one_liner`` LLM 1 회로 사전 압축.

summary 슬롯은 None 으로 둠 → ``step7_analyze_code`` 이 Bottom-up DAG 로 채움.

LLM 호출 = 큰 코드노드만 1 회 (작은 모델).

**성능**: 같은 file 을 토큰 추정 / one_liner 계산에서 두 번 read 하던 것을 1 회로
통합 — ``SourceFileCache`` 가 file_path → 텍스트 라인 캐싱.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig

from shared.files.cache import SourceFileCache
from pipeline.execution.step_config import step_config

from pipeline.steps.step6_build_context.large_code_one_liner import (
    compute_one_liner,
    estimate_tokens,
)
from pipeline.steps.step6_build_context.control_subtree import build_control_subtrees
from pipeline.steps.step6_build_context.referenced_symbols import _build_reference_context
from pipeline.steps.step6_build_context.external_evidence import build_external_evidence_context
from pipeline.models.analysis_context import AnalysisContext
from graph.schema.graph_nodes import CodeNode
from shared.config.settings import SETTINGS
from shared.graphs.call_cycles import find_call_cycles
from llm.usage.cost_budget import BudgetExceededError, CostBudget
from llm.fanout import gather_with_warmup
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.events.processing_events import step_processing_scope, track_processing

_STEP = "step6.build_context"


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
    external_by_fn = build_external_evidence_context(state, code_nodes)

    # 파일 본문 캐시 — 같은 file 여러 코드노드가 공유 (성능: N*M read → N read).
    body_cache = cfg.get("source_cache") or SourceFileCache()   # run 스코프 공유 (FR-008)

    # 컨텍스트 생성 (결정적 메타)
    contexts: dict[str, AnalysisContext] = {}
    control_subtrees = build_control_subtrees(code_nodes)
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
            referenced_symbols=tuple(sorted(refsym_by_fn.get(fn.id, ()), key=lambda symbol: (symbol.kind, symbol.name, symbol.id))),
            external_evidence=external_by_fn.get(fn.id, ()),
            token_count=estimate_tokens(body_text),
        )
        ctx = await builder.augment(ctx, fn, state)
        if fn.id in control_subtrees:
            ctx.extra["control_subtree"] = control_subtrees[fn.id]
        contexts[fn.id] = ctx

    # 큰 코드노드만 one_liner 사전 압축 (LLM 1 회 — model_small).
    # 분석 대상 노드로 한정한다: one_liner 의 세 소비처(callee 요약·자식 치환·조상 골격)는
    # 모두 분석 컨텍스트에 있는 id 만 조회하므로, 대상 밖 노드의 one_liner 는 만들어도
    # 읽힐 수 없다. 실측(SKT 44k): 큰 노드 335개 중 208개가 제어문이라 전량 사장됐다.
    #
    # 나아가 **읽힐 수 있는 자리에 있는지**까지 본다(_one_liner_readable_ids). 대상 노드라도
    # 세 소비처의 관문(부모-자식 관계·순환)에 걸리지 않으면 만들어도 사장된다.
    analysis_targets = strategy.analysis_targets(code_nodes)
    readable = _one_liner_readable_ids(contexts)
    big_code_nodes = [
        fn for fn in code_nodes
        if fn.id in analysis_targets
        and fn.id in readable
        and contexts[fn.id].token_count >= SETTINGS.analysis_pipeline.big_context_threshold_tokens
    ]
    if big_code_nodes:
        log_info(
            _STEP, "one_liner_start",
            big_code_nodes=len(big_code_nodes),
            threshold_tokens=SETTINGS.analysis_pipeline.big_context_threshold_tokens,
            concurrency=SETTINGS.analysis_pipeline.max_concurrency,
        )
        semaphore = asyncio.Semaphore(SETTINGS.analysis_pipeline.max_concurrency)
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


def _one_liner_readable_ids(contexts: dict[str, AnalysisContext]) -> set[str]:
    """one_liner 가 **읽힐 수 있는** 코드노드 id — 소비처 세 곳의 관문에서 역산한다.

    소비처와 그 관문:
    1. callee 요약(``_format_callee_summaries``) — callee 의 summary 가 아직 없을 때만 쓴다.
       Bottom-up 순서상 summary 가 없는 건 **순환 내부**뿐이다.
    2. 자식 치환(``range_body.compact_body``) — ``ctx.extra["child_ids"]`` 가 있어야 도달한다.
    3. 조상 골격(``range_body.ancestor_context``) — ``ctx.extra["parent_id"]`` 가 있어야 도달한다.

    엔진 이름으로 분기하지 않는다. 부모-자식 관계를 채우는 strategy(DBMS 의 nested range)는
    2·3 이 그대로 살아 있고, 안 채우는 strategy 는 관문이 닫혀 자동으로 1 만 남는다.
    같은 코드가 두 엔진에서 각자 옳게 동작한다.
    """
    readable: set[str] = set()
    edges: list[tuple[str, str]] = []
    for ctx in contexts.values():
        parent_id = ctx.extra.get("parent_id")
        if parent_id:
            readable.add(parent_id)                       # 3. 조상 골격
        readable.update(ctx.extra.get("child_ids") or ())  # 2. 자식 치환
        edges.extend((ctx.id, callee_id) for callee_id in ctx.callees)

    # 1. 순환 내부 — callee summary 가 제때 없을 수 있는 유일한 구조적 경우.
    readable.update(find_call_cycles(edges, contexts.keys()))
    return readable


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
