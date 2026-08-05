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
from pipeline.steps.step7_analyze_code.batch_prompt_values import (
    _build_batch_values,
    build_rule_batch_values,
)
from shared.graphs.call_cycles import find_call_cycles
from pipeline.steps.step7_analyze_code.range_body import skeleton_body
from pipeline.steps.step7_analyze_code.rw_table_filter import (
    serialize_rules,
)
from pipeline.steps.step7_analyze_code.control_prompt import build_control_inventory
from pipeline.quality_gates.rule_grading import grade_rules, partition_rule_candidates
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.steps.step5_linking.link_calls.call_linker import routine_call_pairs
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.code_analysis_output import (
    BatchRulesItem,
    BatchRulesOutput,
    BatchSummaryItem,
    BatchTableInsightsItem,
    BatchTableInsightsOutput,
    BatchSummaryOutput,
    RulesExamplesOutput,
)
from pipeline.models.rule_response_schema import (
    batch_rules_response_model,
    normalize_batch_rules_response,
)
from pipeline.models.table_insight_response_schema import (
    batch_table_insights_response_model,
    normalize_batch_table_insights_response,
)
from pipeline.models.summary_response_schema import (
    batch_summary_response_model,
    normalize_batch_summary_response,
)
from pipeline.steps.step7_analyze_code.rule_row_batching import (
    required_rule_rows,
    rule_queue_priority,
    SUMMARY_WAKE_PRIORITY,
)
from graph.schema.graph_nodes import CodeNode
from shared.config.settings import SETTINGS
from pipeline.steps.step7_analyze_code.summary_quality import normalize_canonical_summary
from pipeline.languages import (
    ROUTINE_LABELS,
    require_stereotype,
    exclude_contradicted_stereotype,
)
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache
from pipeline.execution.analysis_handoff import AnalysisHandoff
from pipeline.execution.pause_controller import PauseController
from pipeline.steps.step7_analyze_code.quality_findings import apply_analysis_findings

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

    # run 공유 슬롯 — 후처리와 같은 세마포어(spec 096). 각 concern은 독립 호출이므로
    # summary가 먼저 끝나는 즉시 후속 배치와 후처리에 공개한다.
    semaphore = handoff.slots
    handoff.expect(analysis_ids)
    # source_cache 는 caller(analyze_code_step)가 주입 — run 스코프 공유 (spec 046 FR-008).
    em_acc = ExampleMappingResult()
    analyzed = batch_count = 0

    # Dataflow 스케줄 — 배치 의존(배치 밖 callee) 완료 즉시 투입 (framework 엔진과 동일).
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

    summary_done_nodes: set[str] = set()
    summary_event = asyncio.Event()
    scheduled: set[int] = set()
    pending: dict[asyncio.Task, Any] = {}
    remaining_batches = {id(b): b for b in all_batches}

    def _mark_summaries_ready(node_ids: list[str]) -> None:
        for node_id in node_ids:
            summary_done_nodes.add(node_id)
            node = code_by_id[node_id]
            if node.summary:
                handoff.publish_summary(node)
        summary_event.set()

    try:
        while remaining_batches:
            await pause.gate()
            ready = [b for bid, b in remaining_batches.items()
                     if bid not in scheduled and batch_deps[bid] <= summary_done_nodes]
            for b in ready:
                task = asyncio.ensure_future(
                    _run_batch_with_ref(
                        b,
                        _analyze_batch_guarded(
                            b, contexts, code_by_id, strategy, cost_budget,
                            codebase_notes, module_intents, table_descriptions, source_cache, semaphore,
                            rule_units, rule_gates, _mark_summaries_ready,
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
            waker = asyncio.create_task(summary_event.wait())
            try:
                finished, _ = await asyncio.wait(
                    {*pending, waker}, return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                waker.cancel()
            summary_event.clear()
            finished.discard(waker)
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
                missing_release = batch_nodes[id(b)] - summary_done_nodes
                if missing_release:
                    raise RuntimeError(
                        f"summary-ready 신호 누락: {sorted(missing_release)}"
                    )
                for fn in result:
                    em = await _persist_and_emit_code(fn, neo, stream)
                    _accumulate_em(em_acc, em)
                    analyzed += int(bool(fn.summary))
                # 인계 해제는 **공개 뒤에** — 순서가 뒤집히면 게이트가 열린 뒤에 노드가
                # 실려 후처리가 미완성 목록을 읽는다.
                for node_id in batch_nodes[id(b)]:
                    handoff.resolve(node_id)
    finally:
        # pause/stop/예산 차단/상위 취소 모두 coroutine finally까지 수거한 뒤 진행.
        await pause.cancel_and_wait(list(pending))

    # 모든 배치는 fail-closed다. 정상 반환 뒤 summary가 빠졌다면 수집/적용 배선 결함이다.
    unanalyzed = len(analysis_ids) - analyzed
    if unanalyzed:
        raise RuntimeError(
            f"DBMS 분석 불변식 위반: targets={len(analysis_ids)}, "
            f"summarized={analyzed}, missing={unanalyzed}"
        )

    log_info(
        _STEP, "done",
        analysis_nodes=len(analysis_ids), batches=batch_count,
        analyzed=analyzed, in_cycle=len(cycles),
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

    예외는 값으로 바꾸지 않고 상위 step까지 전파한다.
    """
    return batch, await coro


async def _analyze_batch_guarded(
    batch: Batch, contexts, code_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache, semaphore,
    rule_units: set[str], rule_gates: set[str], on_summaries_ready,
):
    """배치 1개 분석. 운영·결정론 실패만 상위 step까지 전파한다."""
    async with track_processing("Batch", batch.parent_id or batch.node_ids[0]):
        return await _analyze_batch(
            batch, contexts, code_by_id, strategy, cost_budget,
            codebase_notes, module_intents, table_descriptions, source_cache,
            semaphore, rule_units, rule_gates, on_summaries_ready,
        )


async def _analyze_batch(
    batch: Batch, contexts, code_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache, llm_slots,
    rule_units, rule_gates, on_summaries_ready,
) -> list[CodeNode]:
    """배치 형제들을 관심사별로 한 번 분석하고 모델 품질 finding은 격리한다."""
    prompts_dir = Path(strategy.prompts_dir)
    node_ids = list(batch.node_ids)

    pre_findings: dict[str, list[AnalysisFinding]] = {}
    rule_ids = [nid for nid in node_ids if nid in rule_units]
    gate_ids = [nid for nid in node_ids if nid in rule_gates]
    dml_ids = [nid for nid in node_ids if contexts[nid].reads or contexts[nid].writes]
    tasks = {}

    async def _summary_concern():
        try:
            items = await _collect_keyed_concern(
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
                llm_slots=llm_slots,
                strategy_name=strategy.name,
            )
            findings = {}
        except ModelOutputError as error:
            items = {}
            findings = {
                node_id: [AnalysisFinding(
                    concern="summary",
                    status="unavailable",
                    target_id=node_id,
                    reasons=(str(error),),
                    evidence_rule=error.rule_name,
                )]
                for node_id in node_ids
            }
        for node_id in node_ids:
            summary = items.get(node_id)
            if summary is None:
                code_by_id[node_id].extra["questions"] = []
            else:
                contradiction = _apply_batch_summary(
                    summary, code_by_id[node_id], contexts[node_id], strategy,
                )
                if contradiction is not None:
                    findings.setdefault(node_id, []).append(contradiction)
        on_summaries_ready(node_ids)
        return items, findings

    async def _rule_concern(ids, rule_name, *, body_fn=None):
        kwargs = {"body_fn": body_fn} if body_fn is not None else {}
        try:
            items = await _collect_keyed_concern(
                parent_id=batch.parent_id,
                node_ids=ids,
                concern="rules",
                rule_name=rule_name,
                response_model=BatchRulesOutput,
                prompts_dir=prompts_dir,
                contexts=contexts,
                code_by_id=code_by_id,
                cost_budget=cost_budget,
                codebase_notes=codebase_notes,
                module_intents=module_intents,
                table_descriptions=table_descriptions,
                source_cache=source_cache,
                llm_slots=llm_slots,
                strategy_name=strategy.name,
                **kwargs,
            )
            return items, {}
        except ModelOutputError as error:
            findings: dict[str, list[AnalysisFinding]] = {}
            gate_only = rule_name == "code_gate_rules_batch"
            for node_id in ids:
                inventory = build_control_inventory(
                    code_by_id[node_id], contexts[node_id], source_cache,
                )
                findings[node_id] = [
                    AnalysisFinding(
                        concern="rules",
                        status="unavailable",
                        target_id=f"{node_id}::R{row}",
                        coordinates=(row,),
                        reasons=(str(error),),
                        evidence_rule=error.rule_name,
                    )
                    for row in required_rule_rows(inventory, gate_only=gate_only)
                ]
            return {}, findings

    tasks["summary"] = _summary_concern()
    if rule_ids:
        tasks["rules"] = _rule_concern(rule_ids, "code_rules_examples_batch")
    if gate_ids:
        tasks["gate_rules"] = _rule_concern(
            gate_ids, "code_gate_rules_batch", body_fn=skeleton_body,
        )
    if dml_ids:
        async def _insight_concern():
            try:
                items = await _collect_keyed_concern(
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
                    llm_slots=llm_slots,
                    strategy_name=strategy.name,
                )
                return items, {}
            except ModelOutputError as error:
                return {}, {
                    node_id: [AnalysisFinding(
                        concern="table_insights",
                        status="unavailable",
                        target_id=node_id,
                        reasons=(str(error),),
                        evidence_rule=error.rule_name,
                    )]
                    for node_id in dml_ids
                }

        tasks["insights"] = _insight_concern()

    results = await asyncio.gather(*tasks.values())
    done = dict(zip(tasks, results))

    summaries, summary_findings = done["summary"]
    for node_id, findings in summary_findings.items():
        pre_findings.setdefault(node_id, []).extend(findings)

    rules: dict[str, BatchRulesItem] = {}
    rule_findings: dict[str, list[AnalysisFinding]] = {}
    for name in ("rules", "gate_rules"):
        if name in done:
            items, findings = done[name]
            rules.update(items)
            for node_id, node_findings in findings.items():
                rule_findings.setdefault(node_id, []).extend(node_findings)
    if "insights" in done:
        insights, insight_findings = done["insights"]
        for node_id, findings in insight_findings.items():
            pre_findings.setdefault(node_id, []).extend(findings)
    else:
        insights = {}
    for node_id, findings in rule_findings.items():
        pre_findings.setdefault(node_id, []).extend(findings)

    applied: list[CodeNode] = []
    for fid in node_ids:
        requested: set[str] = set()
        if fid in rule_units or fid in rule_gates:
            requested.add("rules")
        if fid in dml_ids:
            requested.add("insights")
        applied.append(_apply_node_analysis(
            summaries.get(fid),
            rules.get(fid),
            insights.get(fid),
            code_by_id[fid],
            contexts[fid],
            strategy,
            source_cache,
            requested_parts=requested,
            is_gate=fid in rule_gates,
            pre_findings=pre_findings.get(fid, ()),
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
    llm_slots,
    strategy_name,
    body_fn=None,
) -> dict[str, Any]:
    """DBMS 키 봉투를 한 번 호출하고 결정론 키 불변식 위반은 실패시킨다."""
    expected = _expected_keys(node_ids, contexts)
    kwargs = {"body_fn": body_fn} if body_fn is not None else {}
    values, _ = _build_batch_values(
        Batch(parent_id, tuple(node_ids)), contexts, code_by_id, codebase_notes,
        module_intents, table_descriptions, source_cache,
        **kwargs,
    )
    rule_assignments = None
    rule_evidences = None
    rule_condition_sources = None
    rule_effect_sources = None
    insight_assignments = None
    effective_response_model = response_model
    if concern == "summary":
        effective_response_model = batch_summary_response_model(
            expected, strategy_name,
        )
    elif concern == "rules":
        gate_only = rule_name == "code_gate_rules_batch"
        rule_assignments = {}
        rule_evidences = {}
        rule_condition_sources = {}
        rule_effect_sources = {}
        for node_key, node_id in expected.items():
            inventory = build_control_inventory(
                code_by_id[node_id], contexts[node_id], source_cache,
            )
            rule_assignments[node_key] = required_rule_rows(
                inventory, gate_only=gate_only,
            )
            evidence = rule_evidence(
                inventory, rule_assignments[node_key],
            )
            rule_evidences[node_key] = evidence
            rule_condition_sources[node_key] = evidence["conditions"]
            rule_effect_sources[node_key] = evidence["effects"]
        effective_response_model = batch_rules_response_model(rule_assignments)
        values = build_rule_batch_values(
            expected=expected,
            assignments=rule_assignments,
            evidences=rule_evidences,
            contexts=contexts,
            code_by_id=code_by_id,
            table_descriptions=table_descriptions,
        )
    elif concern == "insights":
        insight_assignments = {}
        for node_key, node_id in expected.items():
            ctx = contexts[node_id]
            insight_assignments[node_key] = {
                "reads": tuple(ctx.reads),
                "writes": tuple(ctx.writes),
                "table_ops": dict(ctx.extra.get("table_op") or {}),
            }
        effective_response_model = batch_table_insights_response_model(
            insight_assignments
        )

    # Summary alone unlocks bottom-up parents. Give it wake-up priority without changing
    # the shared capacity; Rule/table work remains ahead of post-processing priority 1.
    if concern == "summary":
        queue_priority = SUMMARY_WAKE_PRIORITY
    elif concern == "rules":
        queue_priority = rule_queue_priority(sum(len(str(value)) for value in values.values()))
    else:
        queue_priority = 0
    async with llm_slots.at(queue_priority):
        output = await run_yaml_rule(
            prompts_dir=prompts_dir,
            rule_name=rule_name,
            user_values=values,
            response_model=effective_response_model,
            budget=cost_budget,
        )
    if concern == "summary":
        output = normalize_batch_summary_response(output, expected)
    elif rule_assignments is not None:
        output = normalize_batch_rules_response(
            output,
            rule_assignments,
            rule_condition_sources,
            rule_effect_sources,
        )
    elif insight_assignments is not None:
        output = normalize_batch_table_insights_response(output, insight_assignments)
    accepted, unresolved = _partition_keyed_items(
        output.nodes, expected, concern=concern,
    )
    if unresolved:
        raise ValueError(
            f"DBMS {concern} 키 계약 실패 — target={parent_id or node_ids[0]}, "
            f"unresolved={sorted(unresolved)}"
        )
    return accepted


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


def _partition_keyed_items(items, expected, *, concern: str):
    """Accept exact unique keys; duplicate/missing keys remain unresolved."""
    by_key: dict[str, list[Any]] = {}
    unknown: list[str] = []
    for item in items:
        key = item.node_key.strip()
        if key not in expected:
            unknown.append(key)
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
    if unknown or duplicates or unresolved:
        log_warning(
            _STEP,
            "batch_key_contract",
            "DBMS 배치 키 계약 불일치 — 호출을 실패 처리",
            concern=concern,
            unknown_keys=sorted(set(unknown)) or None,
            duplicate_keys=duplicates or None,
            unresolved_ids=sorted(unresolved) or None,
        )
    return accepted, unresolved


def _apply_batch_summary(
    summary: BatchSummaryItem,
    fn: CodeNode,
    ctx: AnalysisContext,
    strategy: Any,
) -> AnalysisFinding | None:
    """Publish the summary-owned fields without waiting for sibling concerns."""
    summary_text = normalize_canonical_summary(summary.summary)
    stereotype, violation = exclude_contradicted_stereotype(
        require_stereotype(summary.stereotype, strategy.name),
        has_writes=bool(ctx.writes),
    )
    if violation:
        log_warning(
            _STEP, "stereotype_contradiction",
            "stereotype 이 결정론 쓰기 근거와 모순 — 빈 값으로 강제(사람 검토)",
            code_id=fn.id, reason=violation,
        )
    fn.summary = summary_text
    fn.stereotype = stereotype or None
    fn.extra["questions"] = [q.model_dump() for q in summary.questions]
    ctx.summary = summary_text
    if not violation:
        return None
    return AnalysisFinding(
        concern="stereotype",
        status="rejected",
        target_id=fn.id,
        reasons=(violation,),
        candidate={"stereotype": summary.stereotype},
        evidence_rule="code_summary_batch",
    )


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
    is_gate: bool = False,
    pre_findings=(),
) -> CodeNode:
    """3 배치 분할 결과(요약/비즈로직/테이블) → CodeNode + ctx 반영 (three_split fan-in 동일).

    rules/table 은 해당 관심사를 요청하지 않은 노드에서만 None이다.
    """
    findings = list(pre_findings)
    if summary is None:
        # Questions are summary-owned. A missing summary is already a visible finding,
        # but downstream graph construction still requires an explicit empty collection.
        fn.extra.setdefault("questions", [])
    if "insights" in requested_parts:
        fn.extra["table_insights"] = [
            t.model_dump() for t in table.table_insights
        ] if table else []
    if "rules" in requested_parts:
        accepted_rules = []
        inventory = build_control_inventory(fn, ctx, source_cache)
        assigned = set(required_rule_rows(inventory, gate_only=is_gate))
        if rules is not None:
            # 분기표 대조 채점. 빈 rules도 의무 행 누락으로 검증하며 건너뛰지 않는다.
            source_plain = source_cache.slice(
                ctx.file_path, ctx.start_line, ctx.end_line
            )
            report = grade_rules(
                RulesExamplesOutput(
                    rules=rules.rules
                ),
                inventory,
                source_plain,
                assigned=assigned,
                allowed_tables=set(ctx.reads) | set(ctx.writes),
                read_tables=set(ctx.reads),
                write_tables=set(ctx.writes),
                table_ops=ctx.extra.get("table_op") or {},
                require_exact_conditions=True,
            )
            accepted_rules, graded_findings = partition_rule_candidates(
                RulesExamplesOutput(rules=rules.rules),
                report,
                assigned=assigned,
                evidence_rule="code_gate_rules_batch" if is_gate else "code_rules_examples_batch",
            )
            findings.extend(graded_findings)
        rule_finding_records = [
            finding.to_dict() for finding in findings if finding.concern == "rules"
        ]
        fn.extra["rule_grading"] = {
            "obligation_rows": len(assigned),
            "accepted_rows": [rule.anchor_line for rule in accepted_rules],
            "finding_rows": sorted({
                row for item in rule_finding_records for row in item["coordinates"]
            }),
        }
        serialized, unresolved_tables = serialize_rules(
            accepted_rules,
            fn.signature or fn.id,
            ctx.reads,
            ctx.writes,
            ctx.extra.get("table_op"),
        )
        fn.extra["rules"] = serialized
        if unresolved_tables:
            raise ValueError(
                f"{fn.id}: then 테이블 효과가 결정론 R/W와 불일치: "
                f"{sorted(set(unresolved_tables))[:8]}"
            )
    # ctx 에 부착 — 부모 배치가 compact_body·자식규칙으로 라이브 조회.
    if "rules" in requested_parts:
        ctx.extra["rules"] = fn.extra["rules"]
        ctx.extra["questions"] = fn.extra["questions"]
    apply_analysis_findings(fn, findings, component=_STEP)
    return fn
