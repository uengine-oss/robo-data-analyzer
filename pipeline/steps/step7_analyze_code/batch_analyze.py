"""DBMS step7 배치 3분할 분석 (🅕) — 같은 유효 부모 형제 그룹을 관심사별 콜로 분석.

``analyze_code_step`` 의 DBMS 분기 (Framework 는 ``three_split_llm_fanout`` per-node 유지).
framework 와 같은 3 관심사(①요약 ②테이블 ③비즈니스로직), 단 dbms 는 같은 유효부모 형제
N 노드를 **배치(그룹)** 로 묶어 던진다 — 관심사별 1콜씩 총 3콜, ``node_key``(줄범위) 로 분배.

- skip 노드(괄호 DECLARE/TRY/TRIGGER_BLOCK)는 분석 제외 + 컨텍스트 투명(E).
- 바텀업 라운드(유효 트리+CALLS)·persist·EM 누적은 ``analyze_code_step`` 헬퍼 재사용.
- ★ 각 콜은 "한 관심사 × 노드 리스트" 평탄 출력(깊은 중첩 0) → LLM 응답 폭주(length max) 차단.
- 비즈로직(rules) 분할 단위 = **예산 내 최고 조상**(budget descent — 루틴 직속에서 시작, 예산 초과
  컨테이너만 자식으로 하강). 단위 body 는 원문 통째(1차 증거 — ``body_for`` 의 "예산 내 원문" 원리) →
  값 집합·매핑·보정이 전달 게임(요약 경유) 없이 직달. 단위 아래 노드는 규칙 X(파편·중복 차단 원리 유지),
  문맥은 조상 골격(주석 스트립)이 나른다.
- 테이블 분할은 ``reads``/``writes`` 있는 **DML 노드만** 입력(three_split 와 동일 게이트).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Optional

from pipeline.steps.step7_analyze_code.analysis_order import bottom_up_rounds
from pipeline.steps.step7_analyze_code.batch_planner import (
    Batch, build_effective_tree, plan_batches,
)
from pipeline.steps.step7_analyze_code.batch_prompt_values import _build_batch_values
from shared.graphs.call_cycles import find_call_cycles
from pipeline.steps.step7_analyze_code.range_body import skeleton_body
from pipeline.steps.step7_analyze_code.rw_table_filter import constrain_tables_to_rw
from pipeline.steps.step5_linking.link_calls.call_linker import routine_call_pairs
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    BatchRulesOutput,
    BatchSummaryItem,
    BatchTableInsightsItem,
    BatchTableInsightsOutput,
    require_unique_rule_occurrences,
    BatchSummaryOutput,
)
from graph.schema.graph_nodes import CodeNode
from shared.config.settings import SETTINGS
from pipeline.steps.step7_analyze_code.domain_terms import normalize_domain_terms
from pipeline.steps.step7_analyze_code.summary_quality import (
    normalize_searchable_text,
)
from pipeline.languages import ROUTINE_LABELS, coerce_stereotype
from llm.usage.cost_budget import BudgetExceededError
from llm.runners.yaml_llm_runner import run_yaml_rule
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache
from pipeline.execution.analysis_handoff import AnalysisHandoff
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step7_analyze_code.concern_retry import (
    ConcernAttempt,
    run_concern_with_retry,
)

_STEP = "step7.analyze_code.batch"

# rules(비즈니스 규칙) 추출 단위 = "원문(RAW)이 토큰 예산에 들어가는 가장 높은 문장"(budget descent).
# 루틴 직속(레벨1)부터 시작해 예산 초과 컨테이너만 자식으로 내려간다 — 증거는 항상 1차(원문),
# 크기는 예산으로 안전. 단위 아래 노드는 규칙 X(단위가 흡수 = 파편·중복 차단은 레벨1 원리 유지).
#
# 루틴 판정은 ``ontology.ROUTINE_LABELS`` 단일 정의처를 쓴다 (옛 로컬 ``_ROUTINE_TYPES`` 는
# METHOD 가 빠져 있어 실제로 갈라져 있었다 — spec 046 FR-005).


def dbms_analysis_targets(code_nodes: list[CodeNode]) -> set[str]:
    """DBMS 가 실제로 분석하는 노드 = skip-투명 유효 트리에 남는 노드.

    이 엔진과 ``build_context_step`` 이 같은 답을 봐야 해서 여기 한 곳에 둔다.
    skip 노드는 트리에서 투명 처리돼 자식·조상 어느 렌더에도 등장하지 않는다.
    """
    return set(build_effective_tree(code_nodes)[2])


def _plan_rule_units(
    code_by_id: dict[str, CodeNode],
    eff_children: dict[str, list[str]],
    token_of: dict[str, int],
    budget: int,
) -> tuple[set[str], set[str]]:
    """규칙 추출 단위 선정 — 루틴의 각 톱레벨 문장에서 시작해 예산 초과 + 자식 있으면 하강.

    Returns:
        ``(units, gates)`` — units: 예산에 들어가 원문 통째로 규칙을 뽑는 단위(초과인데
        자식 없으면 그대로 단위 — 희귀, solo 배치). gates: 하강하며 지나간 컨테이너 —
        자식이 내용 규칙을 담당하므로, 컨테이너는 **골격(자식 접힘)으로 자기 줄의
        게이트·선별 조건만** 규칙화한다(내용 중복 원천 차단).
    """
    units: set[str] = set()
    gates: set[str] = set()

    def descend(nid: str) -> None:
        kids = eff_children.get(nid, [])
        if token_of.get(nid, 0) <= budget or not kids:
            units.add(nid)
            return
        gates.add(nid)
        for child in kids:
            descend(child)

    for node in code_by_id.values():
        if node.label in ROUTINE_LABELS:
            for child in eff_children.get(node.id, []):
                descend(child)
    return units, gates


async def run_dbms_batch_analysis(
    state: dict,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    strategy: Any,
    cost_budget,
    neo,
    stream,
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, dict],
    pause: PauseController,
    source_cache: SourceFileCache,
    handoff: AnalysisHandoff,
) -> dict:
    """DBMS 배치 분석 — 유효 트리 → 바텀업 라운드 → 라운드별 형제 배치 1콜.

    ``run_framework_per_node_analysis`` 와 **동일 시그니처** — 전략이 둘 중 하나를 주입한다
    (``Strategy.code_analyzer``, 헌법 II / spec 046 FR-001).

    결과는 ``analyze_code_step`` 과 동일한 state dict (analysis_contexts + EM 9종).

    일시정지/중단은 ``pause.gate()`` 로 소비 — Framework ``_run_rounds`` 와 동일 정책:
    paused 면 hold, stop 이면 RunStopped, 일시정지로 취소된 배치는 재개 시 재투입.
    """
    # persist/EM 헬퍼는 analyze_code_step 공유 (지연 import — 순환 회피).
    from pipeline.steps.step7_analyze_code.framework_per_node import _accumulate_em
    from pipeline.steps.step7_analyze_code.result_sink import _persist_and_emit_code
    from pipeline.steps.step7_analyze_code.example_mapping_builder import ExampleMappingResult

    started = time.monotonic()
    code_nodes = list(code_by_id.values())

    # 1. skip-투명 유효 트리 → 컨텍스트 child_ids/parent_id 를 유효값으로 (range_body 투명).
    eff_parent, eff_children, analysis_ids = build_effective_tree(code_nodes)
    _apply_effective_tree(contexts, eff_parent, eff_children, code_by_id)

    # 2. 바텀업 라운드 — 유효 parent→child + CALLS (자식·피호출 먼저).
    # CALLS 는 CALL→대상 저장이므로 루틴쌍으로 해소(단일 해소기) 후 정렬에 사용.
    edges: list[tuple[str, str]] = [(p, c) for c, p in eff_parent.items() if p is not None]
    edges.extend(routine_call_pairs(code_nodes, state.get("calls_edges", [])))
    cycles = find_call_cycles(edges, list(analysis_ids))
    for fn_id in cycles:
        if fn_id in contexts:
            contexts[fn_id].is_in_cycle = True
    rounds = bottom_up_rounds(edges, list(analysis_ids), cycles)

    start_of = {fid: contexts[fid].start_line for fid in analysis_ids if fid in contexts}
    token_of = {fid: contexts[fid].token_count for fid in analysis_ids if fid in contexts}

    # 규칙 추출 단위 = 예산 내 최고 조상 (budget descent). body_for 의 단일 원리(예산 내 원문)와
    # 결합 → 단위 노드의 body 는 자동으로 원문(1차 증거). 하강한 컨테이너(gates)는 골격으로
    # 자기 줄 게이트 조건만 규칙화(자식 내용은 각 단위 담당 — 중복 0).
    rule_units, rule_gates = _plan_rule_units(code_by_id, eff_children, token_of, SETTINGS.analysis_pipeline.batch_max_tokens)
    log_info(_STEP, "rule_units", units=len(rule_units), gates=len(rule_gates), analysis_nodes=len(analysis_ids))

    # run 공유 슬롯 — 후처리와 같은 세마포어(spec 096). 총량은 종전과 같고 우선순위만 다르다.
    # 배치 엔진은 형제를 한 콜로 묶으므로 summary 와 rules 가 동시에 확정된다 → 후처리 인계는
    # 노드 완료 시점 하나뿐이다(per-node 엔진처럼 #1 을 앞당길 자리가 없다).
    semaphore = handoff.slots
    handoff.expect(analysis_ids)
    # source_cache 는 caller(analyze_code_step)가 주입 — run 스코프 공유 (spec 046 FR-008).
    em_acc = ExampleMappingResult()
    analyzed = partial = failed = batch_count = 0

    # Dataflow 스케줄 — 배치 의존(배치 밖 callee) 완료 즉시 투입 (framework 엔진과 동일).
    # 실패 배치의 노드도 의존 해소로 간주(라운드 방식과 동일 의미).
    all_batches: list = []
    batch_nodes: dict[int, set[str]] = {}
    for round_ids in rounds:
        if not round_ids:
            continue
        for b in plan_batches(round_ids, eff_parent, start_of, token_of,
                              SETTINGS.analysis_pipeline.batch_max_tokens):
            all_batches.append(b)
            batch_nodes[id(b)] = set(b.node_ids)   # Batch 계약: node_ids tuple[str, ...]
    batch_count += len(all_batches)

    node_deps: dict[str, set[str]] = {fid: set() for fid in analysis_ids}
    for caller, callee in edges:
        if (caller in node_deps and callee in node_deps and caller != callee
                and cycles.get(caller, caller) != cycles.get(callee, callee)):
            node_deps[caller].add(callee)
    batch_deps: dict[int, set[str]] = {}
    for b in all_batches:
        own = batch_nodes[id(b)]
        batch_deps[id(b)] = set().union(*(node_deps.get(n, set()) for n in own)) - own

    done_nodes: set[str] = set()
    scheduled: set[int] = set()
    pending: dict[asyncio.Task, Any] = {}
    remaining_batches = {id(b): b for b in all_batches}
    try:
        while remaining_batches:
            await pause.gate()
            ready = [b for bid, b in remaining_batches.items()
                     if bid not in scheduled and batch_deps[bid] <= done_nodes]
            for b in ready:
                task = asyncio.ensure_future(
                    _run_batch_with_ref(
                        b,
                        _analyze_batch_guarded(
                            b, contexts, code_by_id, strategy, cost_budget,
                            codebase_notes, module_intents, table_descriptions, source_cache, semaphore,
                            rule_units, rule_gates,
                        ),
                    )
                )
                pending[task] = b
                scheduled.add(id(b))
            if not pending:
                raise RuntimeError(
                    f"dataflow 교착 — 미완 배치 {len(remaining_batches)}개가 준비되지 않음 "
                    "(의존 그래프 결함 의심)"
                )
            pause.set_inflight(list(pending))
            finished, _ = await asyncio.wait(set(pending), return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                b = pending.pop(task)
                if task.cancelled():
                    scheduled.discard(id(b))       # 일시정지 취소 → 재개 시 재투입
                    continue
                _bref, result = task.result()
                if isinstance(result, asyncio.CancelledError):
                    scheduled.discard(id(b))
                    continue
                remaining_batches.pop(id(b), None)
                done_nodes |= batch_nodes[id(b)]   # 실패여도 의존 해소 (기존 라운드 의미)
                if isinstance(result, BudgetExceededError):
                    raise result                   # 회로 차단기 즉시 전파
                if isinstance(result, BaseException):
                    failed += 1
                    log_error(_STEP, "batch", result)
                else:
                    for fn in result:              # 배치가 분석한 CodeNode 들
                        if fn.summary:
                            handoff.publish_summary(fn)   # summary 성공 노드만 후처리 인계
                        em = await _persist_and_emit_code(fn, neo, stream)
                        _accumulate_em(em_acc, em)
                        analyzed += int(bool(fn.summary))
                        partial += int(bool((fn.extra or {}).get("partial")))
                # 인계 해제는 **공개 뒤에** — 순서가 뒤집히면 게이트가 열린 뒤에 노드가
                # 실려 후처리가 미완성 목록을 읽는다.
                for node_id in batch_nodes[id(b)]:
                    handoff.resolve(node_id)
    finally:
        # pause/stop/예산 차단/상위 취소 모두 coroutine finally까지 수거한 뒤 진행.
        await pause.cancel_and_wait(list(pending))

    # 불변식 (spec 045 FR-008): ``failed`` 는 **배치** 단위라 노드 집계와 단위가 다르다.
    # 분석 대상 노드 중 결과를 못 받은 노드는 summary 없이 남아 검색 코퍼스에서 빠진다
    # → 노드 기준으로 직접 검산해 표면화한다 (조용한 유실 금지).
    unanalyzed = len(analysis_ids) - analyzed
    if unanalyzed:
        log_warning(
            _STEP, "nodes_unanalyzed",
            f"분석 대상 {len(analysis_ids)} 중 {unanalyzed}개가 결과 없이 남음 "
            f"(실패 배치 {failed}건). summary 미부여 → 검색 코퍼스에서 제외된다.",
            analysis_nodes=len(analysis_ids), analyzed=analyzed, unanalyzed=unanalyzed,
            failed_batches=failed,
        )

    log_info(
        _STEP, "done",
        analysis_nodes=len(analysis_ids), batches=batch_count,
        analyzed=analyzed, partial=partial or None, failed_batches=failed or None,
        unanalyzed=unanalyzed or None, in_cycle=len(cycles),
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
        "affects_table_edges": em_acc.affects_table,
    }


def _apply_effective_tree(
    contexts: dict[str, AnalysisContext],
    eff_parent: dict[str, str | None],
    eff_children: dict[str, list[str]],
    code_by_id: dict[str, CodeNode],
) -> None:
    """컨텍스트의 child_ids/parent_id 를 유효(skip-투명) 값으로 덮어씀 → range_body 투명 동작.

    ``label`` (노드 타입)도 함께 채운다 — compact 치환이 `[범위] TYPE: 요약` 으로 자식이 별개
    문장임을 명시(요약이 산문처럼 이어져 여러 결정이 한 규칙으로 뭉치는 것 방지).
    """
    for fid, ctx in contexts.items():
        ctx.extra["parent_id"] = eff_parent.get(fid)
        ctx.extra["child_ids"] = tuple(eff_children.get(fid, ()))
        node = code_by_id.get(fid)
        if node is not None:
            ctx.extra["label"] = node.label


async def _run_batch_with_ref(batch: Batch, coro):
    """coro 결과를 ``(batch, result)`` 로 묶음 — ``as_completed`` 가 어느 배치인지 매칭 가능하게.

    ``_analyze_batch_guarded`` 가 예외(취소 포함)를 값으로 반환하므로 여기선 그대로 전달.
    """
    return batch, await coro


async def _analyze_batch_guarded(
    batch: Batch, contexts, code_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache, semaphore,
    rule_units: set[str], rule_gates: set[str],
):
    """배치 1개 분석 — 예외를 값으로 묶어 반환 (as_completed 매칭). 성공 시 CodeNode 리스트."""
    async with track_processing("Batch", batch.parent_id or batch.node_ids[0]):
        async with semaphore:
            try:
                return await _analyze_batch(
                    batch, contexts, code_by_id, strategy, cost_budget,
                    codebase_notes, module_intents, table_descriptions, source_cache,
                    rule_units, rule_gates,
                )
            except BudgetExceededError as e:
                return e
            except BaseException as e:  # noqa: BLE001 — 매칭 위해 값으로 반환
                return e


async def _analyze_batch(
    batch: Batch, contexts, code_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache, rule_units, rule_gates,
) -> list[CodeNode]:
    """배치 형제들을 관심사별로 분석하고 실패한 관심사·키만 한 번 재요청한다."""
    prompts_dir = Path(strategy.prompts_dir)
    node_ids = list(batch.node_ids)

    # Summary는 공통 prefix 워밍을 위해 먼저 끝낸다. 누락 키만 같은 Summary 계약으로
    # 재요청하며, 성공한 Summary는 아래 어떤 호출도 다시 만들지 않는다.
    summaries, summary_failed = await _collect_keyed_concern(
        parent_id=batch.parent_id,
        node_ids=node_ids,
        concern="summary",
        rule_name="code_summary_batch",
        response_model=BatchSummaryOutput,
        prompts_dir=prompts_dir,
        contexts=contexts,
        code_by_id=code_by_id,
        cost_budget=cost_budget,
        codebase_notes=codebase_notes,
        module_intents=module_intents,
        table_descriptions=table_descriptions,
        source_cache=source_cache,
    )

    rule_ids = [nid for nid in node_ids if nid in rule_units]
    gate_ids = [nid for nid in node_ids if nid in rule_gates]
    dml_ids = [nid for nid in node_ids if contexts[nid].reads or contexts[nid].writes]
    tasks = {}
    if rule_ids:
        tasks["rules"] = _collect_keyed_concern(
            parent_id=batch.parent_id,
            node_ids=rule_ids,
            concern="rules",
            rule_name="code_rules_examples_batch",
            response_model=BatchRulesOutput,
            prompts_dir=prompts_dir,
            contexts=contexts,
            code_by_id=code_by_id,
            cost_budget=cost_budget,
            codebase_notes=codebase_notes,
            module_intents=module_intents,
            table_descriptions=table_descriptions,
            source_cache=source_cache,
        )
    if gate_ids:
        tasks["gate_rules"] = _collect_keyed_concern(
            parent_id=batch.parent_id,
            node_ids=gate_ids,
            concern="rules",
            rule_name="code_gate_rules_batch",
            response_model=BatchRulesOutput,
            prompts_dir=prompts_dir,
            contexts=contexts,
            code_by_id=code_by_id,
            cost_budget=cost_budget,
            codebase_notes=codebase_notes,
            module_intents=module_intents,
            table_descriptions=table_descriptions,
            source_cache=source_cache,
            body_fn=skeleton_body,
        )
    if dml_ids:
        tasks["insights"] = _collect_keyed_concern(
            parent_id=batch.parent_id,
            node_ids=dml_ids,
            concern="insights",
            rule_name="code_table_insights_batch",
            response_model=BatchTableInsightsOutput,
            prompts_dir=prompts_dir,
            contexts=contexts,
            code_by_id=code_by_id,
            cost_budget=cost_budget,
            codebase_notes=codebase_notes,
            module_intents=module_intents,
            table_descriptions=table_descriptions,
            source_cache=source_cache,
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    done = dict(zip(tasks, results))
    for r in results:
        if isinstance(r, BudgetExceededError):
            raise r
        if isinstance(r, BaseException):
            raise r

    rules: dict[str, BatchRulesItem] = {}
    rules_failed: set[str] = set()
    for name in ("rules", "gate_rules"):
        if name in done:
            values, unresolved = done[name]
            rules.update(values)
            rules_failed.update(unresolved)
    insights, insights_failed = done.get("insights", ({}, set()))

    applied: list[CodeNode] = []
    for fid in node_ids:
        requested = {"summary"}
        if fid in rule_units or fid in rule_gates:
            requested.add("rules")
        if fid in dml_ids:
            requested.add("insights")
        failed_parts = {
            part for part, failed_ids in (
                ("summary", summary_failed),
                ("rules", rules_failed),
                ("insights", insights_failed),
            )
            if fid in failed_ids
        }
        applied.append(_apply_node_analysis(
            summaries.get(fid),
            rules.get(fid),
            insights.get(fid),
            code_by_id[fid],
            contexts[fid],
            strategy,
            source_cache,
            requested_parts=requested,
            failed_parts=failed_parts,
        ))
    return applied


async def _collect_keyed_concern(
    *,
    parent_id,
    node_ids,
    concern,
    rule_name,
    response_model,
    prompts_dir,
    contexts,
    code_by_id,
    cost_budget,
    codebase_notes,
    module_intents,
    table_descriptions,
    source_cache,
    body_fn=None,
) -> tuple[dict[str, Any], set[str]]:
    """Preserve unique successes and retry only unresolved DBMS keys once."""
    expected = _expected_keys(node_ids, contexts)

    async def _request(ids, max_attempts):
        async def _call():
            kwargs = {"body_fn": body_fn} if body_fn is not None else {}
            values, _ = _build_batch_values(
                Batch(parent_id, tuple(ids)),
                contexts,
                code_by_id,
                codebase_notes,
                module_intents,
                table_descriptions,
                source_cache,
                **kwargs,
            )
            return await run_yaml_rule(
                prompts_dir=prompts_dir,
                rule_name=rule_name,
                user_values=values,
                response_model=response_model,
                budget=cost_budget,
            )

        return await run_concern_with_retry(
            component=_STEP,
            concern=concern,
            target_id=parent_id or ids[0],
            call=_call,
            max_attempts=max_attempts,
        )

    first: ConcernAttempt = await _request(node_ids, 2)
    if not first.succeeded:
        return {}, set(node_ids)

    accepted, unresolved = _partition_keyed_items(
        first.value.nodes, expected, concern=concern, attempt=1,
    )
    if not unresolved:
        return accepted, set()

    retry_ids = [expected[key] for key in expected if expected[key] in unresolved]
    retry: ConcernAttempt = await _request(retry_ids, 1)
    if not retry.succeeded:
        return accepted, unresolved

    retry_expected = _expected_keys(retry_ids, contexts)
    recovered, still = _partition_keyed_items(
        retry.value.nodes, retry_expected, concern=concern, attempt=2,
    )
    accepted.update(recovered)
    return accepted, still


def _expected_keys(node_ids, contexts) -> dict[str, str]:
    expected: dict[str, str] = {}
    for nid in node_ids:
        ctx = contexts[nid]
        key = f"{ctx.start_line}-{ctx.end_line}"
        if key in expected:
            raise ValueError(
                f"duplicate DBMS node_key {key!r}: {expected[key]!r}, {nid!r}"
            )
        expected[key] = nid
    return expected


def _partition_keyed_items(items, expected, *, concern: str, attempt: int):
    """Accept exact unique keys; duplicate/missing keys remain unresolved."""
    by_key: dict[str, list[Any]] = {}
    unknown: list[str] = []
    invalid: list[str] = []
    for item in items:
        key = item.node_key.strip()
        if key not in expected:
            unknown.append(key)
            continue
        if isinstance(item, BatchRulesItem):
            try:
                require_unique_rule_occurrences(item.rules)
            except ValueError:
                invalid.append(key)
                continue
        by_key.setdefault(key, []).append(item)

    duplicates = sorted(key for key, values in by_key.items() if len(values) != 1)
    accepted = {
        expected[key]: values[0]
        for key, values in by_key.items()
        if len(values) == 1
    }
    unresolved = {
        nid for key, nid in expected.items()
        if key not in by_key or key in duplicates
    }
    if unknown or duplicates or invalid or unresolved:
        log_warning(
            _STEP,
            "batch_key_contract",
            "DBMS 배치 키 계약 불일치 — 정상 키는 보존하고 미해결 키만 재시도",
            concern=concern,
            attempt=attempt,
            unknown_keys=sorted(set(unknown)) or None,
            duplicate_keys=duplicates or None,
            invalid_item_keys=sorted(set(invalid)) or None,
            unresolved_ids=sorted(unresolved) or None,
        )
    return accepted, unresolved


def _apply_node_analysis(
    summary: Optional[BatchSummaryItem],
    rules: Optional[BatchRulesItem],
    table: Optional[BatchTableInsightsItem],
    fn: CodeNode,
    ctx: AnalysisContext,
    strategy: Any,
    source_cache: SourceFileCache,
    *,
    requested_parts: set[str],
    failed_parts: set[str],
) -> CodeNode:
    """3 배치 분할 결과(요약/비즈로직/테이블) → CodeNode + ctx 반영 (three_split fan-in 동일).

    rules/table 은 조각 실패·비-DML 이면 None → 빈 배열(부분 성공 허용, 요약만 코어).
    """
    if summary is not None:
        summary_text, search_text = normalize_searchable_text(
            summary.summary, summary.search_text
        )
        fn.summary = summary_text
        fn.stereotype = coerce_stereotype(summary.stereotype or "", strategy.name) or None
        fn.logical_name = summary.logical_name or None
        fn.search_text = search_text
        excerpt = source_cache.excerpt(
            ctx.file_path, ctx.start_line, ctx.end_line
        )
        fn.domain_terms = normalize_domain_terms(
            summary.domain_terms,
            source_text=excerpt.raw_text,
            numbered_source_text=excerpt.numbered_text,
            code_id=fn.id,
            component=_STEP,
        )
        ctx.summary = summary_text
    # spec 027: LLM 귀속 테이블을 결정론 reads/writes 로 제한 (callee 함수명 오귀속 차단).
    constrain_tables_to_rw(
        fn.id, ctx.reads, ctx.writes, table, rules,
        allowed_columns_by_table=ctx.extra.get("ddl_columns_by_table"),
        table_op=ctx.extra.get("table_op"),
    )
    if "insights" in requested_parts:
        fn.extra["table_insights"] = [
            t.model_dump() for t in table.table_insights
        ] if table else []
    if "rules" in requested_parts:
        fn.extra["rules"] = [r.model_dump() for r in rules.rules] if rules else []
        fn.extra["questions"] = [q.model_dump() for q in rules.questions] if rules else []
    if failed_parts:
        fn.extra["partial"] = tuple(
            part for part in ("summary", "rules", "insights") if part in failed_parts
        )
    else:
        fn.extra.pop("partial", None)
    # ctx 에 부착 — 부모 배치가 compact_body·자식규칙으로 라이브 조회.
    if "rules" in requested_parts:
        ctx.extra["rules"] = fn.extra["rules"]
        ctx.extra["questions"] = fn.extra["questions"]
    return fn
