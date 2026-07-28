"""step7_analyze_code — 모듈 사전분석 + Bottom-up DAG + 3 분할 fan-out.

순서:
0. 모듈 사전분석 (Framework 전용) — 모듈마다 LLM 1회로 헤더 주석 → 모듈 의도.
   결과 ``module_intent`` 는 아래 코드노드 분석의 3분할 공통 context 에 주입.
1. 호출 그래프 (CALLS + PARENT_OF) → ``find_call_cycles`` → cycle 식별 (Tarjan SCC).
2. 의존 그래프(callee→caller) 구성 — dataflow 스케줄 (spec 079, 라운드 배리어 제거):
   의존이 풀린 코드노드부터 즉시 분석, 끝나는 순서대로 즉시 Neo4j 영속화 + NDJSON emit.
   의존 해제 시점은 종류별로 다르다 — CALLS 의존은 callee 의 summary 확정 신호로
   조기 해제(caller 프롬프트는 summary 한 줄만 읽음), PARENT_OF 의존은 자식의
   rules·questions 까지 필요해 전체 완료를 기다린다. 투입 순서는 critical-path
   rank 내림차순(긴 사슬·큰 노드 우선) — 전부 산출물 불변인 스케줄 결정이다.
3. 각 코드노드 = ``analyze_code_three_split`` (3 분할 fan-out, RadixAttention 95%+).

cycle 내부 코드노드 → callee summary 없이 분석 (``is_in_cycle=True`` 메타).
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, Optional


from shared.concurrency.priority_semaphore import PrioritySemaphore
from shared.files.cache import SourceFileCache
from pipeline.execution.analysis_handoff import AnalysisHandoff
from pipeline.execution.pause_controller import PauseController

# (dbms 엔진은 여기서 import 하지 않는다 — 전략이 주입한다(analysis_pipelines). 헌법 II.)
from shared.graphs.call_cycles import find_call_cycles
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    analyze_code_three_split,
    run_summary,
)
from shared.config.settings import SETTINGS
from pipeline.steps.step7_analyze_code.example_mapping_builder import ExampleMappingResult
from llm.usage.cost_budget import BudgetExceededError
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.languages import ROUTINE_LABELS
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.events.ndjson_event_stream import NDJSONStream
from shared.events.processing_events import track_processing
from graph.neo4j_client import Neo4jClient

_STEP = "step7.analyze_code"


from pipeline.steps.step7_analyze_code.result_sink import _persist_and_emit_code


def framework_analysis_targets(code_nodes: list) -> set[str]:
    """Framework 가 실제로 분석하는 노드 = routine 라벨만 (spec 054 FR-005).

    이 엔진과 ``build_context_step`` 이 같은 답을 봐야 해서 여기 한 곳에 둔다 —
    분석 대상 밖 노드는 아래에서 컨텍스트에서 빠지므로 어느 렌더에서도 조회되지 않는다.
    parser 가 emit 하는 제어문(IF/LOOP/…)은 그래프엔 남지만 LLM 분석 대상이 아니다.
    """
    return {fn.id for fn in code_nodes if fn.label in ROUTINE_LABELS}


async def run_framework_per_node_analysis(
    state: dict,
    contexts: dict,
    code_by_id: dict,
    strategy,
    cost_budget,
    neo: Neo4jClient | None,
    stream: Optional[NDJSONStream],
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, dict],
    pause: PauseController,
    source_cache: SourceFileCache,
    handoff: AnalysisHandoff,
) -> dict:
    """Framework 분석 엔진 — 코드노드 **1개당** 3분할 fan-out (per-node).

    ``run_dbms_batch_analysis`` 와 **동일 시그니처** — 전략이 둘 중 하나를 주입한다
    (``Strategy.code_analyzer``). 호출부(``analyze_code_step``)는 어느 쪽인지 모른다.

    caller 가 ``step_processing_scope`` 진입 + ``flush_drop_summary`` + ``handoff`` 종료를
    책임진다.
    """
    started = time.monotonic()

    # 0. 분석 대상 = routine 만 (spec 054 FR-005). parser 007 이 emit 하는 제어문(IF/LOOP/…)은
    #    reader 가 그래프에 CodeNode 로 보존하지만 routine 이 아니므로 LLM 분석에서 제외한다.
    #    ``ROUTINE_LABELS`` 가 단일 진실(DBMS batch 와 동일) — 소비자마다 if 분기 흩뿌리지 않는다.
    #    이 한 곳에서 걸러 all_ids/rounds/accounting/return 이 모두 routine 기준으로 흐른다.
    targets = framework_analysis_targets(state.get("code_nodes", []))
    non_routine = [cid for cid in contexts if cid not in targets]
    if non_routine:
        contexts = {cid: ctx for cid, ctx in contexts.items() if cid not in non_routine}
        log_info(_STEP, "analysis_target_filter",
                 routines=len(contexts), excluded_structural=len(non_routine))

    # 1. cycle 식별 (CALLS + PARENT_OF 둘 다) — Framework per-node 경로.
    #    all_ids 가 routine 뿐이라 제어문 PARENT_OF 는 endpoint 가 all_ids 밖 → 순서 계산에 오염 X.
    #    (주의: 제어문 아래 중첩 함수(Python nested def 등)의 함수→함수 순서는 제어문이 끊는다.
    #     C 는 중첩 함수가 없어 무영향 — Java/Python 확장 시 routine 투과 containment 재검토.)
    call_edges: list[tuple[str, str]] = [
        (e.caller_id, e.callee_id)
        for e in state.get("calls_edges", [])
    ]
    parent_edges: list[tuple[str, str]] = [
        (e.parent_id, e.child_id)
        for e in state.get("parent_of_edges", [])
    ]
    all_ids = list(contexts.keys())
    cycles = find_call_cycles(call_edges + parent_edges, all_ids)

    # cycle 메타 ctx 에 부착
    for fn_id in cycles:
        if fn_id in contexts:
            contexts[fn_id].is_in_cycle = True

    # 2. 의존 그래프 — 의존 종류에 따라 해제 시점이 다르다 (SCC 내부 의존은 끊음).
    #    CALLS: caller 의 프롬프트는 callee 의 summary 한 줄만 읽는다 → summary 확정 즉시 해제.
    #    PARENT_OF: 부모의 #3 분할은 자식 rules·questions 를 읽는다 → 전체 완료까지 대기.
    summary_deps = _build_dependency_map(call_edges, contexts, cycles)
    full_deps = _build_dependency_map(parent_edges, contexts, cycles)
    ranks = _dispatch_ranks(contexts, code_by_id, summary_deps, full_deps)
    log_info(
        _STEP, "start",
        code_nodes=len(contexts),
        summary_dep_edges=sum(len(s) for s in summary_deps.values()),
        full_dep_edges=sum(len(s) for s in full_deps.values()),
        in_cycle=len(cycles),
        concurrency=SETTINGS.analysis_pipeline.max_concurrency,
    )

    # 3. Dataflow 병렬 분석 — 의존 완료 즉시 투입. 결과는 코드노드 단위 즉시 영속화 + emit.
    # Example Mapping 9 종 (Rule/Example/Question + 6 엣지) 은 코드노드 단위로 만들어져
    # round 끝까지 누적 → step 마지막에 state 로 반영. Neo4j MERGE 가 정체성 키
    # (rule_hash / example_id / question_id) 로 자동 dedup — state list 는 raw 그대로.
    # run 공유 슬롯 — 후처리와 **같은 세마포어**를 쓴다(spec 096). step7 은 우선순위 0
    # 이라 후처리보다 항상 먼저 슬롯을 잡는다. 총량은 종전과 같다(값 = max_concurrency).
    semaphore = handoff.slots
    handoff.expect(contexts.keys())
    # source_cache 는 caller(analyze_code_step)가 주입 — run 스코프 공유 (FR-008).
    em_acc = ExampleMappingResult()

    async def _run_dataflow() -> tuple[int, int, int]:
        """의존 완료 즉시 투입하는 dataflow 스케줄 — 라운드 배리어의 꼬리 유휴 제거.

        CALLS 의존은 callee 의 summary 확정 신호(``on_summary_ready``)로 조기 해제하고,
        PARENT_OF 의존만 전체 완료를 기다린다. 투입 순서는 ``ranks`` 내림차순 —
        긴 의존 사슬·큰 노드를 먼저 넣어 종반 꼬리 유휴를 줄인다(산출물 불변).
        취소된 노드만 재투입, ``analyzed+failed+skipped = code_nodes`` 등식 유지(FR-008).
        """
        analyzed_count = 0
        failed_count = 0
        skipped_count = 0
        done: set[str] = set()
        summary_done: set[str] = set()
        scheduled: set[str] = set()
        pending: dict[asyncio.Task, str] = {}
        summary_event = asyncio.Event()
        warm_id: Optional[str] = None
        first_summary: Optional[SummaryOutput] = None

        def _mark_summary_ready(fid: str) -> None:
            summary_done.add(fid)
            summary_event.set()
            node = code_by_id.get(fid)
            if node is not None:
                # 후처리(모듈요약·컴포넌트명·용어사전)는 이 시점의 필드만 읽는다 — 노드
                # 완료가 아니라 여기서 인계해야 step7 의 빈 슬롯을 채울 수 있다 (spec 096).
                handoff.publish_summary(node)

        def _ready() -> list[str]:
            return sorted(
                (
                    fid for fid in contexts
                    if fid not in done and fid not in scheduled
                    and summary_deps[fid] <= summary_done and full_deps[fid] <= done
                ),
                key=lambda fid: ranks[fid],
                reverse=True,
            )

        try:
            while len(done) < len(contexts):
                await pause.gate()

                ready = _ready()
                if warm_id is None and ready:
                    # 워밍 1회 — 공통 prefix 를 서버 캐시에 올린 뒤 fan-out.
                    warm_id = ready[0]
                    first_summary = await pause.run_restartable_during_pause(
                        lambda: run_summary(
                            warm_id, contexts, code_by_id, strategy, cost_budget,
                            codebase_notes, module_intents, table_descriptions, source_cache,
                        )
                    )

                for fn_id in ready:
                    task = asyncio.ensure_future(
                        _run_with_id(
                            fn_id,
                            _analyze_one(
                                fn_id, contexts, code_by_id, strategy, cost_budget,
                                semaphore, codebase_notes, module_intents, table_descriptions,
                            source_cache,
                            precomputed_summary=(first_summary if fn_id == warm_id else None),
                            precomputed_summary_supplied=(fn_id == warm_id),
                            on_summary_ready=_mark_summary_ready,
                            ),
                        )
                    )
                    pending[task] = fn_id
                    scheduled.add(fn_id)

                if not pending:
                    # 조용한 교착 금지 — 즉시 표면화.
                    raise RuntimeError(
                        f"dataflow 교착 — 미완 {len(contexts) - len(done)}개 코드노드가 "
                        "준비되지 않음 (의존 그래프 결함 의심)"
                    )

                pause.set_inflight(list(pending))
                # summary 확정 신호로도 깨어난다 — 조기 해제된 caller 를 즉시 투입하기 위함.
                waker = asyncio.ensure_future(summary_event.wait())
                try:
                    finished, _ = await asyncio.wait(
                        {*pending, waker}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    waker.cancel()
                summary_event.clear()   # 신호의 진실은 summary_done — 이벤트는 wake 전용.
                finished.discard(waker)
                for task in finished:
                    fn_id = pending.pop(task)
                    if task.cancelled():
                        scheduled.discard(fn_id)  # 일시정지 취소 → 재개 시 재투입
                        continue
                    _rid, r = task.result()
                    if isinstance(r, asyncio.CancelledError):
                        scheduled.discard(fn_id)  # 〃
                        continue
                    done.add(fn_id)               # 성공·분석실패·None = 처리 완료 (재시도 X)
                    summary_done.add(fn_id)       # 실패·스킵 노드도 caller 를 막지 않는다
                    handoff.resolve(fn_id)        # 후처리 대기에서도 빼야 게이트가 열린다
                    if isinstance(r, BudgetExceededError):
                        raise r                   # 절대원칙 #3 — 회로 차단기 즉시 전파
                    if isinstance(r, BaseException):
                        failed_count += 1
                        log_error(_STEP, "node_analysis", r, code_id=fn_id)
                        continue
                    if r is None:
                        # LLM 이 결과를 못 냄 — 어디에도 안 세면 조용히 실종된다 (FR-008).
                        skipped_count += 1
                        log_warning(
                            _STEP, "node_skipped",
                            "코드노드가 분석 결과 없이 종료 — summary 미부여 → 검색 코퍼스에서 제외된다.",
                            code_id=fn_id,
                        )
                        continue
                    em = await _persist_and_emit_code(r, neo, stream)
                    _accumulate_em(em_acc, em)
                    analyzed_count += 1
        finally:
            # pause/stop/예산 차단/상위 취소 모두 coroutine finally 까지 수거한 뒤 진행.
            await pause.cancel_and_wait(list(pending))
        return analyzed_count, failed_count, skipped_count

    # step_processing_scope / flush_drop_summary 는 caller(analyze_code_step) 책임 —
    # dbms 엔진과 대칭(둘 다 caller 가 감싼다). 여기서 또 진입하면 중첩된다.
    total_analyzed, total_failed, total_skipped = await _run_dataflow()

    # 불변식: analyzed + failed + skipped = 대상 코드노드 수 (FR-008). 안 맞으면 조용한 유실.
    _accounted = total_analyzed + total_failed + total_skipped
    if _accounted != len(contexts):
        log_warning(
            _STEP, "node_accounting_mismatch",
            f"집계 불일치 — 대상 {len(contexts)} ≠ analyzed {total_analyzed} + "
            f"failed {total_failed} + skipped {total_skipped} ({_accounted}). 조용한 유실 신호.",
            expected=len(contexts), accounted=_accounted,
        )
    log_info(
        _STEP, "done",
        code_nodes=len(contexts), analyzed=total_analyzed,
        failed=total_failed or None, skipped=total_skipped or None, in_cycle=len(cycles),
        rules=len(em_acc.rules), examples=len(em_acc.examples), questions=len(em_acc.questions),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    # code_nodes 는 _persist_and_emit_code 가 in-place 갱신 (frozen=False)
    # → state["code_nodes"] 리스트 안의 같은 객체 참조라 자동 반영.
    # Example Mapping 9 종은 누적 결과를 state 키 9 개로 반환.
    return {
        "analysis_contexts": contexts,
        "rules": em_acc.rules,
        "examples": em_acc.examples,
        "questions": em_acc.questions,
        "has_rule_edges": em_acc.has_rule,
        "has_example_edges": em_acc.has_example,
        "has_question_edges": em_acc.has_question,
        "affects_table_edges": em_acc.affects_table,
    }


def _build_dependency_map(
    edge_pairs: list[tuple[str, str]],
    contexts: dict,
    cycles: dict[str, str],
) -> dict[str, set[str]]:
    """의존 맵 — a 가 b 를 기다린다(a→b). contexts 밖·자기자신·같은 SCC 의존은 제외."""
    deps: dict[str, set[str]] = {fid: set() for fid in contexts}
    for a, b in edge_pairs:
        if (a in contexts and b in contexts and a != b
                and cycles.get(a, a) != cycles.get(b, b)):
            deps[a].add(b)
    return deps


def _dispatch_ranks(
    contexts: dict,
    code_by_id: dict,
    summary_deps: dict[str, set[str]],
    full_deps: dict[str, set[str]],
) -> dict[str, int]:
    """투입 우선순위 — 자기 비용(줄 수) + 나를 기다리는 사슬의 최대 rank (critical path).

    긴 의존 사슬의 아래쪽(callee·자식)과 큰 노드를 먼저 투입해 종반에 소수 노드만
    남아 GPU 가 노는 꼬리 유휴를 줄인다. 신호는 범용(코드 줄 수·의존 구조)뿐 —
    프로젝트 특화 판단은 없다. SCC 는 이미 끊겨 DAG 전제가 성립한다.
    """
    def _cost(fid: str) -> int:
        fn = code_by_id.get(fid)
        start = getattr(fn, "start_line", None) or 0
        end = getattr(fn, "end_line", None) or 0
        return max(1, end - start)

    waiters: dict[str, list[str]] = {fid: [] for fid in contexts}
    for deps in (summary_deps, full_deps):
        for waiter, needed in deps.items():
            for dep in needed:
                waiters[dep].append(waiter)

    # 나를 기다리는 노드(waiter)들이 전부 rank 된 뒤에 내 rank 확정 — Kahn 위상 처리.
    unranked_waiters = {fid: len(waiters[fid]) for fid in contexts}
    queue = [fid for fid, n in unranked_waiters.items() if n == 0]
    ranks: dict[str, int] = {}
    while queue:
        fid = queue.pop()
        ranks[fid] = _cost(fid) + max((ranks[w] for w in waiters[fid]), default=0)
        for dep in summary_deps[fid] | full_deps[fid]:
            unranked_waiters[dep] -= 1
            if unranked_waiters[dep] == 0:
                queue.append(dep)
    leftover = [fid for fid in contexts if fid not in ranks]
    if leftover:
        # SCC 절단 후에도 순환이 남았다는 신호 — 교착은 아니므로 비용만으로 투입.
        log_warning(
            _STEP, "dispatch_rank_leftover",
            "우선순위 위상 처리가 닿지 못한 노드 — 자기 비용만으로 투입한다.",
            count=len(leftover),
        )
        for fid in leftover:
            ranks[fid] = _cost(fid)
    return ranks


def _accumulate_em(acc: ExampleMappingResult, em: ExampleMappingResult) -> None:
    """코드노드 단위 ``ExampleMappingResult`` 를 round 누적 결과에 합친다.

    중복 제거는 Neo4j MERGE 가 정체성 키(rule_hash 등)로 자동 처리 — state 는 단순 extend.
    Rule 노드는 ``rule_hash`` 키로 코드노드 간 공유 (같은 statement = 같은 노드).

    새 필드 추가 시 ``ExampleMappingResult`` 의 list 필드면 자동 누적 — dataclass 의
    ``fields`` 가 단일 진실.
    """
    for f in dataclasses.fields(em):
        new = getattr(em, f.name)
        if isinstance(new, list):
            getattr(acc, f.name).extend(new)


async def _run_with_id(fn_id: str, coro) -> tuple[str, Any]:
    """coro 의 결과 또는 예외를 ``(fn_id, value)`` tuple 로 묶음 — ``asyncio.as_completed``
    가 task 객체만 yield 하므로 호출자가 어느 fn_id 의 결과인지 매칭 가능.

    예외는 raise 안 하고 tuple 의 두 번째 자리에 — 호출자가 isinstance 체크로 분기.
    """
    try:
        result = await coro
    except BaseException as e:  # noqa: BLE001 — 예외도 매칭 가능하게 묶어 반환
        return (fn_id, e)
    return (fn_id, result)


async def _analyze_one(
    fn_id: str,
    contexts: dict,
    code_by_id: dict,
    strategy: Any,
    cost_budget,
    semaphore: PrioritySemaphore,
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, dict],
    source_cache: SourceFileCache,
    precomputed_summary: Optional[SummaryOutput] = None,
    precomputed_summary_supplied: bool = False,
    on_summary_ready=None,
):
    """코드노드 1개 분석 — track_processing 이 start/done/failed 자동 emit (stream 인자 chain X)."""
    async with track_processing("CodeNode", fn_id):
        async with semaphore:
            return await analyze_code_three_split(
                fn_id, contexts, code_by_id, strategy,
                cost_budget=cost_budget,
                codebase_notes=codebase_notes,
                module_intents=module_intents,
                table_descriptions=table_descriptions,
                source_cache=source_cache,
                precomputed_summary=precomputed_summary,
                precomputed_summary_supplied=precomputed_summary_supplied,
                on_summary_ready=on_summary_ready,
            )


# ─── 코드노드 분석 결과 영속화 + 스트림 ────────────────────────────
# 코드노드 1건 분석 끝나면 즉시 호출 (batch 누적 X). 메모리 누적 0 + 그래프 실시간.
