"""DBMS step6 배치 분석 (🅕) — 같은 유효 부모 형제를 1 LLM 콜로 분석.

``analyze_function_step`` 의 DBMS 분기 (Framework 는 ``three_split_llm_fanout`` 유지).
per-node(노드당 2~3콜) → 배치(형제 N 노드 1콜) → ``node_key``(줄범위) 로 결과 분배.

- skip 노드(괄호 DECLARE/TRY/TRIGGER_BLOCK)는 분석 제외 + 컨텍스트 투명(E).
- 바텀업 라운드(유효 트리+CALLS)·persist·EM 누적은 ``analyze_function_step`` 헬퍼 재사용.
- 배치 출력은 평탄 리스트(``BatchAnalysisOutput``) — 자유키 dict 폭주 차단.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional

from analysis_flow.step6_analyze_function.analysis_order import bottom_up_rounds
from analysis_flow.step6_analyze_function.batch_planner import (
    Batch, build_effective_tree, plan_batches,
)
from analysis_flow.step6_analyze_function.call_cycles import find_call_cycles
from analysis_flow.step6_analyze_function.range_body import ancestor_context, body_for
from data_models.analysis_context import AnalysisContext
from data_models.function_analysis_output import BatchAnalysisOutput, NodeAnalysis
from data_models.graph_nodes import Function
from shared.config.env_catalog import ENV
from shared.lang.ontology import coerce_stereotype
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_info, log_warning
from shared.log.processing_progress import track_processing
from shared.source_file_cache import SourceFileCache
from shared.workflow.pause_controller import PauseController

_STEP = "step6.analyze_function.batch"


async def run_dbms_batch_analysis(
    state: dict,
    contexts: dict[str, AnalysisContext],
    function_by_id: dict[str, Function],
    strategy: Any,
    cost_budget,
    neo,
    stream,
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, str],
    pause: PauseController,
) -> dict:
    """DBMS 배치 분석 — 유효 트리 → 바텀업 라운드 → 라운드별 형제 배치 1콜.

    결과는 ``analyze_function_step`` 과 동일한 state dict (analysis_contexts + EM 9종).

    일시정지/중단은 ``pause.gate()`` 로 소비 — Framework ``_run_rounds`` 와 동일 정책:
    paused 면 hold, stop 이면 RunStopped, 일시정지로 취소된 배치는 재개 시 재투입.
    """
    # persist/EM 헬퍼는 analyze_function_step 공유 (지연 import — 순환 회피).
    from analysis_flow.step6_analyze_function.analyze_function_step import (
        _accumulate_em, _persist_and_emit_function,
    )
    from shared.example_mapping.builder import ExampleMappingResult

    started = time.monotonic()
    functions = list(function_by_id.values())

    # 1. skip-투명 유효 트리 → 컨텍스트 child_ids/parent_id 를 유효값으로 (range_body 투명).
    eff_parent, eff_children, analysis_ids = build_effective_tree(functions)
    _apply_effective_tree(contexts, eff_parent, eff_children)

    # 2. 바텀업 라운드 — 유효 parent→child + CALLS (자식·피호출 먼저).
    edges: list[tuple[str, str]] = [(p, c) for c, p in eff_parent.items() if p is not None]
    edges.extend(
        (e.caller_function_id, e.callee_function_id) for e in state.get("calls_edges", [])
    )
    cycles = find_call_cycles(edges, list(analysis_ids))
    for fn_id in cycles:
        if fn_id in contexts:
            contexts[fn_id].is_in_cycle = True
    rounds = bottom_up_rounds(edges, list(analysis_ids), cycles)

    start_of = {fid: contexts[fid].start_line for fid in analysis_ids if fid in contexts}
    token_of = {fid: contexts[fid].token_count for fid in analysis_ids if fid in contexts}

    semaphore = asyncio.Semaphore(ENV.pipeline.max_concurrency)
    source_cache = SourceFileCache()
    em_acc = ExampleMappingResult()
    analyzed = failed = batch_count = 0

    for round_idx, round_ids in enumerate(rounds, start=1):
        if not round_ids:
            continue
        batches = plan_batches(round_ids, eff_parent, start_of, token_of, ENV.pipeline.batch_max_tokens)
        batch_count += len(batches)

        # 미완료 배치가 빌 때까지 투입 — 일시정지로 취소된 배치만 재투입(Neo4j MERGE 라 안전).
        remaining = list(batches)
        while remaining:
            await pause.gate()
            tasks = [
                asyncio.ensure_future(
                    _run_batch_with_ref(
                        b,
                        _analyze_batch_guarded(
                            b, contexts, function_by_id, strategy, cost_budget,
                            codebase_notes, module_intents, table_descriptions, source_cache, semaphore,
                        ),
                    )
                )
                for b in remaining
            ]
            pause.set_inflight(tasks)
            done_batches: set[int] = set()
            try:
                for finished in asyncio.as_completed(tasks):
                    batch, result = await finished
                    if isinstance(result, asyncio.CancelledError):
                        continue                   # 일시정지로 취소 → remaining 에 남겨 재투입
                    done_batches.add(id(batch))
                    if isinstance(result, BudgetExceededError):
                        raise result               # 회로 차단기 즉시 전파
                    if isinstance(result, BaseException):
                        failed += 1
                        log_error(_STEP, "batch", result, round=round_idx)
                        continue
                    for fn in result:              # 배치가 분석한 Function 들
                        em = await _persist_and_emit_function(fn, neo, stream)
                        _accumulate_em(em_acc, em)
                        analyzed += 1
            finally:
                pause.clear_inflight()
                for task in tasks:                 # budget 차단 등 조기 이탈 시 orphan LLM 호출 차단
                    if not task.done():
                        task.cancel()
            remaining = [b for b in remaining if id(b) not in done_batches]

    log_info(
        _STEP, "done",
        analysis_nodes=len(analysis_ids), batches=batch_count,
        analyzed=analyzed, failed=failed or None, in_cycle=len(cycles),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "analysis_contexts": contexts,
        "rules": em_acc.rules,
        "examples": em_acc.examples,
        "questions": em_acc.questions,
        "has_rule_edges": em_acc.has_rule,
        "has_example_edges": em_acc.has_example,
        "has_question_edges": em_acc.has_question,
        "next_edges": em_acc.next_edges,
        "branch_edges": em_acc.branch_edges,
        "affects_table_edges": em_acc.affects_table,
    }


def _apply_effective_tree(
    contexts: dict[str, AnalysisContext],
    eff_parent: dict[str, str | None],
    eff_children: dict[str, list[str]],
) -> None:
    """컨텍스트의 child_ids/parent_id 를 유효(skip-투명) 값으로 덮어씀 → range_body 투명 동작."""
    for fid, ctx in contexts.items():
        ctx.extra["parent_id"] = eff_parent.get(fid)
        ctx.extra["child_ids"] = tuple(eff_children.get(fid, ()))


async def _run_batch_with_ref(batch: Batch, coro):
    """coro 결과를 ``(batch, result)`` 로 묶음 — ``as_completed`` 가 어느 배치인지 매칭 가능하게.

    ``_analyze_batch_guarded`` 가 예외(취소 포함)를 값으로 반환하므로 여기선 그대로 전달.
    """
    return batch, await coro


async def _analyze_batch_guarded(
    batch: Batch, contexts, function_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache, semaphore,
):
    """배치 1개 분석 — 예외를 값으로 묶어 반환 (as_completed 매칭). 성공 시 Function 리스트."""
    async with track_processing("Batch", batch.parent_id or batch.node_ids[0]):
        async with semaphore:
            try:
                return await _analyze_batch(
                    batch, contexts, function_by_id, strategy, cost_budget,
                    codebase_notes, module_intents, table_descriptions, source_cache,
                )
            except BudgetExceededError as e:
                return e
            except BaseException as e:  # noqa: BLE001 — 매칭 위해 값으로 반환
                return e


async def _analyze_batch(
    batch: Batch, contexts, function_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache,
) -> list[Function]:
    """배치 형제들을 1 LLM 콜로 분석 → node_key 분배. 누락 노드는 1회 미니 재요청 (누락 0 보장)."""
    applied, missing = await _batch_call(
        batch.parent_id, list(batch.node_ids), contexts, function_by_id, strategy,
        cost_budget, codebase_notes, module_intents, table_descriptions, source_cache,
    )
    if missing:
        # 미니 재요청 — 누락 노드만 다시 1콜 (단독 배치라 적중률 ↑). silent fail 금지.
        retry, still = await _batch_call(
            batch.parent_id, missing, contexts, function_by_id, strategy,
            cost_budget, codebase_notes, module_intents, table_descriptions, source_cache,
        )
        applied.extend(retry)
        if still:
            log_warning(
                _STEP, "missing_after_retry",
                "미니 재요청 후에도 누락 — 그래프 노드는 유지(요약만 빈 채)",
                parent=batch.parent_id, missing_count=len(still),
            )
    return applied


async def _batch_call(
    parent_id, node_ids, contexts, function_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache,
) -> tuple[list[Function], list[str]]:
    """node_ids 1콜 분석 → ``(적용된 Function 들, 누락 node_id 들)``."""
    tmp = Batch(parent_id, tuple(node_ids))
    user_values, key_to_id = _build_batch_values(
        tmp, contexts, function_by_id, codebase_notes,
        module_intents, table_descriptions, source_cache,
    )
    out: BatchAnalysisOutput = await run_yaml_rule(
        prompts_dir=Path(strategy.prompts_dir),
        rule_name="function_batch",
        user_values=user_values,
        response_model=BatchAnalysisOutput,
        budget=cost_budget,
    )
    by_key = {na.node_key.strip(): na for na in out.nodes}
    applied: list[Function] = []
    missing: list[str] = []
    for key, fid in key_to_id.items():
        na = by_key.get(key)
        if na is None:
            missing.append(fid)
            continue
        applied.append(_apply_node_analysis(na, function_by_id[fid], contexts[fid], strategy))
    return applied, missing


def _apply_node_analysis(
    na: NodeAnalysis, fn: Function, ctx: AnalysisContext, strategy: Any,
) -> Function:
    """NodeAnalysis → Function + ctx 반영 (three_split fan-in 과 동일 매핑)."""
    fn.summary = na.summary
    fn.stereotype = coerce_stereotype(na.stereotype or "", strategy.name) or None
    fn.logical_name = na.logical_name or None
    fn.extra["table_insights"] = [t.model_dump() for t in na.table_insights]
    fn.extra["rules"] = [r.model_dump() for r in na.rules]
    fn.extra["examples"] = [e.model_dump() for e in na.examples]
    fn.extra["questions"] = [q.model_dump() for q in na.questions]
    # ctx 에 부착 — 부모 배치가 compact_body·자식규칙으로 라이브 조회.
    ctx.summary = na.summary
    ctx.extra["rules"] = fn.extra["rules"]
    ctx.extra["questions"] = fn.extra["questions"]
    return fn


def _build_batch_values(
    batch: Batch,
    contexts: dict[str, AnalysisContext],
    function_by_id: dict[str, Function],
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, str],
    cache: SourceFileCache,
) -> tuple[dict, dict[str, str]]:
    """배치 → batch yaml user_values + ``node_key -> function_id`` 매핑.

    프로시저 정보(루트)·조상 골격은 배치 1회 공유(prefix 캐시). 각 형제는 줄범위 key +
    reads/writes/참조심볼 + body(자식 있으면 compact = 자식 요약 합본).
    """
    first_ctx = contexts[batch.node_ids[0]]
    root = _root_function(batch.node_ids[0], contexts, function_by_id)
    parent_ctx = contexts.get(batch.parent_id) if batch.parent_id else None
    ancestor = ancestor_context(first_ctx, contexts, cache) if parent_ctx is not None else "(없음)"

    blocks: list[str] = []
    key_to_id: dict[str, str] = {}
    for nid in batch.node_ids:
        ctx = contexts[nid]
        key = f"{ctx.start_line}-{ctx.end_line}"
        key_to_id[key] = nid
        body = body_for(ctx, contexts, cache)
        blocks.append(
            f"### 노드 {key}\n"
            f"reads: {_fmt_tables(ctx.reads, table_descriptions)}\n"
            f"writes: {_fmt_tables(ctx.writes, table_descriptions)}\n"
            f"참조심볼: {', '.join(ctx.referenced_symbols) or '(없음)'}\n"
            f"body:\n```\n{body}\n```"
        )

    values = {
        "unit_id": root.function_id if root else first_ctx.function_id,
        "unit_signature": root.signature if root else first_ctx.signature,
        "module_intent": module_intents.get(root.module_id if root else "") or "(없음)",
        "codebase_notes": codebase_notes or "(없음)",
        "ancestor_context": ancestor,
        "node_count": str(len(batch.node_ids)),
        "node_keys": ", ".join(key_to_id),
        "nodes": "\n\n".join(blocks),
    }
    return values, key_to_id


def _root_function(
    node_id: str, contexts: dict[str, AnalysisContext], function_by_id: dict[str, Function],
) -> Optional[Function]:
    """노드의 프로시저 루트 Function — 유효 parent_id 체인 최상위."""
    cur = node_id
    seen: set[str] = set()
    while cur not in seen:
        seen.add(cur)
        ctx = contexts.get(cur)
        pid = ctx.extra.get("parent_id") if ctx else None
        if pid is None:
            break
        cur = pid
    return function_by_id.get(cur)


def _fmt_tables(fqns: tuple[str, ...], table_descriptions: dict[str, str]) -> str:
    """테이블 fqn 목록 → 한 줄 (설명 있으면 부기). 빈 목록은 '(없음)'."""
    if not fqns:
        return "(없음)"
    parts = []
    for fqn in fqns:
        desc = table_descriptions.get(fqn, "")
        parts.append(f"{fqn}({desc})" if desc else fqn)
    return ", ".join(parts)
