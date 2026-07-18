"""step6_analyze_code — 모듈 사전분석 + Bottom-up DAG + 3 분할 fan-out.

순서:
0. 모듈 사전분석 (Framework 전용) — 모듈마다 LLM 1회로 헤더 주석 → 모듈 의도.
   결과 ``module_intent`` 는 아래 코드노드 분석의 3분할 공통 context 에 주입.
1. 호출 그래프 (CALLS + PARENT_OF) → ``find_call_cycles`` → cycle 식별 (Tarjan SCC).
2. ``bottom_up_rounds`` → leaf → root Round 별 정렬.
3. Round 별 병렬 분석:
   - 같은 Round 코드노드들 ``asyncio.as_completed`` + ``Semaphore`` 병렬 — 끝나는 순서대로
     즉시 Neo4j 영속화 + NDJSON emit (★ 라운드 단위 묶음 X — 라운드는 내부 concurrency
     제어 + 메타 정보일 뿐).
   - Round 간 순차 — 다음 Round 의 caller 가 이전 Round 의 callee summary 사용.
4. 각 코드노드 = ``analyze_code_three_split`` (3 분할 fan-out, RadixAttention 95%+).

cycle 내부 코드노드 → callee summary 없이 분석 (``is_in_cycle=True`` 메타).
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from source.file_cache import SourceFileCache
from pipeline.execution.graph_delta_stream import emit_edges, emit_nodes
from pipeline.execution.pause_controller import NULL_PAUSE, PauseController
from pipeline.execution.step_config import step_config

from pipeline.steps.step6_analyze_code.analysis_order import bottom_up_rounds
# (dbms 엔진은 여기서 import 하지 않는다 — 전략이 주입한다(analysis_pipelines). 헌법 II.)
from pipeline.steps.step6_analyze_code.call_cycles import find_call_cycles
from pipeline.steps.step6_analyze_code.module_pre_analysis import run_module_pre_analysis
from pipeline.steps.step6_analyze_code.rw_table_filter import flush_drop_summary, reset_drop_stats
from pipeline.steps.step6_analyze_code.three_split_llm_fanout import (
    analyze_code_three_split,
    run_summary,
)
from graph.schema.graph_nodes import CodeNode
from analyzer_settings import SETTINGS
from pipeline.steps.step6_analyze_code.example_mapping_builder import (
    ExampleMappingResult,
    build_example_mapping,
)
from graph.writers.example_writer import write_example_mapping
from llm.usage.cost_budget import BudgetExceededError
from pipeline.models.code_analysis_output import SummaryOutput
from pipeline.languages import ROUTINE_LABELS
from runtime_events.runtime_logger import log_error, log_info, log_warning
from runtime_events.ndjson_event_stream import NDJSONStream
from runtime_events.processing_events import step_processing_scope, track_processing
from graph.neo4j_client import Neo4jClient
from graph.nodes.code_node import update_summaries

_STEP = "step6.analyze_code"


from pipeline.steps.step6_analyze_code.result_sink import _persist_and_emit_code


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
) -> dict:
    """Framework 분석 엔진 — 코드노드 **1개당** 3분할 fan-out (per-node).

    ``run_dbms_batch_analysis`` 와 **동일 시그니처** — 전략이 둘 중 하나를 주입한다
    (``Strategy.code_analyzer``). 호출부(``analyze_code_step``)는 어느 쪽인지 모른다.

    caller 가 ``step_processing_scope`` 진입 + ``flush_drop_summary`` 를 책임진다.
    """
    started = time.monotonic()

    # 0. 분석 대상 = routine 만 (spec 054 FR-005). parser 007 이 emit 하는 제어문(IF/LOOP/…)은
    #    reader 가 그래프에 CodeNode 로 보존하지만 routine 이 아니므로 LLM 분석에서 제외한다.
    #    ``ROUTINE_LABELS`` 가 단일 진실(DBMS batch 와 동일) — 소비자마다 if 분기 흩뿌리지 않는다.
    #    이 한 곳에서 걸러 all_ids/rounds/accounting/return 이 모두 routine 기준으로 흐른다.
    label_by_id = {fn.id: fn.label for fn in state.get("code_nodes", [])}
    non_routine = [cid for cid in contexts if label_by_id.get(cid) not in ROUTINE_LABELS]
    if non_routine:
        contexts = {cid: ctx for cid, ctx in contexts.items() if cid not in non_routine}
        log_info(_STEP, "analysis_target_filter",
                 routines=len(contexts), excluded_structural=len(non_routine))

    # 1. cycle 식별 (CALLS + PARENT_OF 둘 다) — Framework per-node 경로.
    #    all_ids 가 routine 뿐이라 제어문 PARENT_OF 는 endpoint 가 all_ids 밖 → 순서 계산에 오염 X.
    #    (주의: 제어문 아래 중첩 함수(Python nested def 등)의 함수→함수 순서는 제어문이 끊는다.
    #     C 는 중첩 함수가 없어 무영향 — Java/Python 확장 시 routine 투과 containment 재검토.)
    edges: list[tuple[str, str]] = [
        (e.caller_id, e.callee_id)
        for e in state.get("calls_edges", [])
    ]
    edges.extend(
        (e.parent_id, e.child_id)
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
        code_nodes=len(contexts), rounds=len(rounds), in_cycle=len(cycles),
        concurrency=SETTINGS.analysis_pipeline.max_concurrency,
    )

    # 3. Round 별 병렬 분석 — Round 간은 순차. 결과는 코드노드 단위 즉시 영속화 + emit.
    # Example Mapping 9 종 (Rule/Example/Question + 6 엣지) 은 코드노드 단위로 만들어져
    # round 끝까지 누적 → step 마지막에 state 로 반영. Neo4j MERGE 가 정체성 키
    # (rule_hash / example_id / question_id) 로 자동 dedup — state list 는 raw 그대로.
    semaphore = asyncio.Semaphore(SETTINGS.analysis_pipeline.max_concurrency)
    # source_cache 는 caller(analyze_code_step)가 주입 — run 스코프 공유 (FR-008).
    em_acc = ExampleMappingResult()

    async def _run_rounds() -> tuple[int, int, int]:
        """Round 루프 — 메인 변수 closure 캡처 (rounds / contexts / em_acc / neo / stream 등).

        ``step_processing_scope`` 안에서 호출 — track_processing 이 자동 emit.

        일시정지/중단은 ``pause.gate()`` 로 소비: paused 면 hold, stop 이면 RunStopped.
        일시정지 순간 in-flight 코드노드 task 는 controller 가 즉시 cancel — 취소된 코드노드만
        ``remaining`` 에 남아 재개 시 재투입(Neo4j MERGE 라 재실행 안전).
        반환: ``(total_analyzed, total_failed, total_skipped)``.

        ``skipped`` = LLM 이 결과를 못 낸(None) 코드노드. **analyzed 로도 failed 로도 안 세면
        어디에도 안 잡힌 채 사라진다** → summary 없는 노드가 검색 코퍼스에서 영구 실종
        (spec 045 FR-008). ``analyzed + failed + skipped = code_nodes`` 등식이 성립해야 한다.
        """
        analyzed_count = 0
        failed_count = 0
        skipped_count = 0
        for round_idx, round_fn_ids in enumerate(rounds, start=1):
            if not round_fn_ids:
                continue
            round_started = time.monotonic()
            log_info(_STEP, "round_start", round=f"{round_idx}/{len(rounds)}", code_nodes=len(round_fn_ids))

            # === Round 워밍 (라운드당 1회) ===
            # 첫 코드노드의 #1 summary 만 단독 선호출 → LLM 서버 prefix cache 에 공통지침 올림.
            # 그 후 모든 코드노드가 자기 흐름(#1 → #2 #3)으로 동시 진행, 그 prefix 에 적중.
            first_id = round_fn_ids[0]
            first_summary: Optional[SummaryOutput] = await pause.run_restartable_during_pause(
                lambda: run_summary(
                    first_id, contexts, code_by_id, strategy, cost_budget,
                    codebase_notes, module_intents, table_descriptions, source_cache,
                )
            )

            # === 미완료분이 빌 때까지 투입 — 일시정지로 취소된 코드노드만 재투입 ===
            # 끝나는 순서 = 영속화/emit 순서 (라운드 단위 X — 라운드는 내부 concurrency 제어).
            # asyncio.as_completed 가 완료 즉시 yield → Neo4j SET + NDJSON emit 즉시.
            # 첫 코드노드는 워밍 결과(precomputed_summary) 재사용.
            remaining = list(round_fn_ids)
            round_failed = 0
            round_analyzed = 0
            round_skipped = 0
            while remaining:
                await pause.gate()
                tasks = [
                    asyncio.ensure_future(
                        _run_with_id(
                            fn_id,
                            _analyze_one(
                                fn_id, contexts, code_by_id, strategy, cost_budget,
                                semaphore, codebase_notes, module_intents, table_descriptions,
                                source_cache,
                                precomputed_summary=(first_summary if fn_id == first_id else None),
                            ),
                        )
                    )
                    for fn_id in remaining
                ]
                pause.set_inflight(tasks)
                done_ids: set[str] = set()
                try:
                    for finished in asyncio.as_completed(tasks):
                        fn_id, r = await finished
                        if isinstance(r, asyncio.CancelledError):
                            continue          # 일시정지로 취소 → remaining 에 남겨 재개 시 재투입
                        done_ids.add(fn_id)   # 성공·분석실패·None = 처리 완료 (재시도 X)
                        if isinstance(r, BudgetExceededError):
                            raise r           # 절대원칙 #3 — 회로 차단기 즉시 전파
                        if isinstance(r, BaseException):
                            round_failed += 1
                            log_error(_STEP, "round_analysis", r, code_id=fn_id, round=round_idx)
                            continue
                        if r is None:
                            # LLM 이 결과를 못 냄 — 어디에도 안 세면 조용히 실종된다 (FR-008).
                            round_skipped += 1
                            log_warning(
                                _STEP, "node_skipped",
                                "코드노드가 분석 결과 없이 종료 — summary 미부여 → 검색 코퍼스에서 제외된다.",
                                code_id=fn_id, round=round_idx,
                            )
                            continue
                        em = await _persist_and_emit_code(r, neo, stream)
                        _accumulate_em(em_acc, em)
                        round_analyzed += 1
                finally:
                    # pause/stop/예산 차단/상위 취소 모두 coroutine finally까지 수거한 뒤 진행.
                    await pause.cancel_and_wait(tasks)
                remaining = [fn_id for fn_id in remaining if fn_id not in done_ids]

            failed_count += round_failed
            analyzed_count += round_analyzed
            skipped_count += round_skipped
            log_info(
                _STEP, "round_done",
                round=f"{round_idx}/{len(rounds)}",
                analyzed=round_analyzed, failed=round_failed or None,
                skipped=round_skipped or None,
                elapsed_sec=f"{time.monotonic() - round_started:.2f}",
            )
        return analyzed_count, failed_count, skipped_count

    # step_processing_scope / flush_drop_summary 는 caller(analyze_code_step) 책임 —
    # dbms 엔진과 대칭(둘 다 caller 가 감싼다). 여기서 또 진입하면 중첩된다.
    total_analyzed, total_failed, total_skipped = await _run_rounds()

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
    semaphore: asyncio.Semaphore,
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, dict],
    source_cache: SourceFileCache,
    precomputed_summary: Optional[SummaryOutput] = None,
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
            )


# ─── 코드노드 분석 결과 영속화 + 스트림 ────────────────────────────
# 코드노드 1건 분석 끝나면 즉시 호출 (batch 누적 X). 메모리 누적 0 + 그래프 실시간.
