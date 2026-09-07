"""DBMS step7 분석 — 같은 유효 부모 형제의 독립 관심사를 함께 예약한다.

``analyze_code_step`` 의 DBMS 분기 (Framework 는 ``three_split_llm_fanout`` per-node 유지).
Summary는 정확한 노드 원문마다 한 번 호출하고, Rule atom meaning과 table-usage meaning은 같은
유효부모 형제를 관심사별 batch로 묶는다. Rule 구조와 좌표는 routine branch inventory에서
결정론으로 만들고 request-local ``N`` key에 분배하며, 모델은 배정된 atom 의미만 채운다.
Rule 구조와 관계는 검증된 결과에서 결정론적으로 만든다.

- skip 노드(괄호 DECLARE/TRY/TRIGGER_BLOCK)는 분석 제외 + 컨텍스트 투명(E).
- 바텀업 라운드(유효 트리+CALLS)·persist·EM 누적은 ``analyze_code_step`` 헬퍼 재사용.
- ★ 각 콜은 한 관심사만 소유한다. Summary는 한 노드, Rule/TABLE은 평탄한 노드 map을 반환한다.
- 비즈로직(rules) 분할 단위 = **예산 내 최고 조상**(budget descent — 루틴 직속에서 시작, 예산 초과
  컨테이너만 자식으로 하강). 단위 body 는 정확한 원문(1차 증거) →
  값 집합·매핑·보정이 전달 게임(요약 경유) 없이 직달. 단위 아래 노드는 규칙 X(파편·중복 차단 원리 유지),
  문맥은 조상 골격(주석 스트립)이 나른다.
- 테이블 분할은 ``reads``/``writes`` 있는 **DML 노드만** 입력(three_split 와 동일 게이트).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from pipeline.meaning.analysis_order import (
    bottom_up_rounds,
    plan_summary_dependencies,
)
from pipeline.meaning.batch_planner import (
    Batch, build_effective_tree, plan_batches,
)
from pipeline.meaning.table.prompt_values import (
    build_table_usage_meaning_batch_values,
)
from pipeline.meaning.batch_node_identity import batch_node_keys
from pipeline.meaning.source_body import exact_source_body
from pipeline.meaning.source_outline import render_source_outline
from pipeline.meaning.three_split_llm_fanout import (
    _direct_callee_effects_by_id,
    _project_referenced_symbols,
    _rule_input_counts,
    _run_frame_summary,
    _summary_input_counts,
    _table_input_counts,
)
from pipeline.source_languages import ROUTINE_LABELS
from pipeline.meaning.table.rule_tables import (
    serialize_rules,
)
from pipeline.meaning.rule.control_inventory import (
    build_control_inventory,
    routine_inventory,
)
from pipeline.meaning.rule.row_ownership import (
    owning_routine,
    plan_rule_row_ownership,
)
from pipeline.meaning.rule.branch_audit import persist_routine_audits
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import exact_direct_table_ids
from pipeline.models.analysis_finding import AnalysisFinding
from pipeline.models.code_analysis_output import (
    BatchFunctionTableUsagesItem,
    BatchRulesItem,
    BatchSummaryItem,
)
from pipeline.meaning.table.response_schema import (
    batch_function_table_usages_response_model,
    function_table_usage_prompt_assignments,
    normalize_batch_function_table_usages_response,
)
from pipeline.meaning.rule.row_batching import (
    RowCollection,
    apply_rule_meaning_projections,
    materialize_and_validate_rules,
)
from pipeline.meaning.rule.meaning_frame import (
    RULE_MEANING_EFFECT_BUDGET,
    batch_rule_meaning_response_model,
    build_rule_meaning_packet,
    partition_rule_meaning_evidence,
    prepare_rule_meaning_assignment,
    project_batch_rule_meanings,
    unavailable_rule_meaning_projections,
)
from graph.schema.graph_nodes import CodeNode
from shared.config.app_settings import SETTINGS
from pipeline.meaning.summary.summary_quality import (
    copied_callee_summary_id,
    missing_structural_summary_identities,
    normalize_canonical_summary,
    unsupported_code_comparison_claims,
)
from pipeline.source_languages import (
    ROUTINE_LABELS,
    require_stereotype,
    exclude_contradicted_stereotype,
)
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.model_output_error import ModelOutputError
from shared.events.runtime_logger import log_info, log_warning
from shared.events.processing_events import track_processing
from shared.files.source_cache import SourceFileCache
from pipeline.execution.analysis_handoff import (
    AnalysisHandoff,
    TABLE_USAGE_MEANINGS_LANE,
    RULES_LANE,
    SUMMARY_LANE,
    SUMMARY_WAKE_PRIORITY,
)
from pipeline.execution.pause_controller import PauseController
from pipeline.execution.semantic_artifacts import persist_function_semantic_frame
from pipeline.meaning.summary.function_meaning import finalize_function_meaning
from pipeline.meaning.quality_findings import record_code_analysis_findings
from pipeline.meaning.prompt_assets import semantic_prompt_dir
from pipeline.models.semantic_frame import (
    mark_semantic_frame_insufficient,
    merge_semantic_frames,
)

_STEP = "step7.analyze_code.batch"


@dataclass(frozen=True)
class DbmsRuleMaterialization:
    """Trusted Rule items and owner-scoped quality findings from one validation boundary."""

    items: dict[str, BatchRulesItem]
    findings: dict[str, list[AnalysisFinding]]
    evidence: dict[str, dict[str, object]]


def _is_summary_terminal(fn: CodeNode, ctx: AnalysisContext) -> bool:
    """Summary concern의 terminal outcome: trusted 값 또는 명시적 품질 거절."""
    return bool((fn.summary or "").strip()) or (
        ctx.extra.get("summary_status") in {"rejected", "needs_context"}
    )

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
    stream,
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
    # 결과/EM 헬퍼는 analyze_code_step 공유 (지연 import — 순환 회피).
    from pipeline.meaning.framework_per_node import _accumulate_rule_graph
    from pipeline.meaning.result_sink import finalize_and_emit_code_result
    from pipeline.meaning.rule.rule_graph import RuleGraphResult

    started = time.monotonic()
    code_nodes = list(code_by_id.values())

    # 1. skip-투명 유효 트리 → 컨텍스트 child_ids/parent_id 를 유효값으로 정규화.
    eff_parent, eff_children, analysis_ids = build_effective_tree(code_nodes)
    _apply_effective_tree(contexts, eff_parent, eff_children, code_by_id)

    # 2. 바텀업 라운드 — prompt 증거 수신자(ctx.callees) + 유효 parent→child.
    # 프로시저 호출 그래프에 이 구조 부모 관계를 섞지 않는다. 같은 SCC에서는 선행 제공이
    # 불가능한 call-summary 의존만 plan이 제거하고 child-before-parent는 보존한다(spec 120).
    structural_edges = [(p, c) for c, p in eff_parent.items() if p is not None]
    analysis_contexts = {node_id: contexts[node_id] for node_id in analysis_ids}
    dependency_plan = plan_summary_dependencies(analysis_contexts, structural_edges)
    edges = list(dependency_plan.schedule_edges)
    rounds = bottom_up_rounds(edges, analysis_ids)

    start_of = {fid: contexts[fid].start_line for fid in analysis_ids if fid in contexts}
    token_of = {fid: contexts[fid].token_count for fid in analysis_ids if fid in contexts}

    # 규칙 추출 단위 = 예산 내 최고 조상 (budget descent). body_for 의 단일 원리(예산 내 원문)와
    # 결합 → 단위 노드의 body 는 자동으로 원문(1차 증거). 하강한 컨테이너(gates)는 골격으로
    # 자기 줄 게이트 조건만 규칙화(자식 내용은 각 단위 담당 — 중복 0).
    rule_units, rule_gates = _plan_rule_units(code_by_id, eff_children, token_of, SETTINGS.analysis_pipeline.batch_max_tokens)

    # 의무 소유자는 **루틴**이다 (spec 117). 위 단위 선정은 입력 본문을 좁히는 역할만 하고,
    # 각 배치 노드는 루틴 분기표에서 자기 줄 범위의 행만 배분받는다. 종전에는 배치 노드마다
    # 자기 분기표를 만들었는데 statement/control 사실이 루틴 id 로만 집계되므로 전부 0행이
    # 되어 46콜이 빈 의무로 나가고 findings 0 으로 통과했다(2026-08-07 실측, 헌법 IV).
    routine_inventories: dict[str, Any] = {}

    def _routine_inventory(routine: CodeNode):
        inventory = routine_inventories.get(routine.id)
        if inventory is None:
            inventory = build_control_inventory(
                routine, contexts[routine.id], source_cache,
            )
            routine_inventories[routine.id] = inventory
        return inventory

    row_plan = plan_rule_row_ownership(
        sorted(rule_units | rule_gates), code_by_id, _routine_inventory,
    )
    # 분기표와 exact Rule 구조 정답지를 **루틴당 한 번** 남긴다. 이 산출물과 Rule meaning
    # provider 기록과 함수별 배치 결과의 수를 직접 대조한다.
    # 종전에 이 기록이 framework 엔진에만 있어 DBMS 는 [A] 가 항상 0 이었고,
    # 헌법 IX 2층이 DBMS 에서 성립하지 않았다(2026-08-07 실측).
    persist_routine_audits(routine_inventories, code_by_id, row_plan=row_plan)
    for node_id, rows in row_plan.items():
        node_ctx = contexts.get(node_id)
        if node_ctx is None:
            raise ValueError(f"rule 의무를 배분할 컨텍스트가 없다: {node_id!r}")
        node_ctx.extra["rule_rows"] = rows
    # 빈 의무 배치는 LLM 을 호출하지 않는다 (헌법 IV — 빈 성공 금지).
    rule_gates = {node_id for node_id in rule_gates if row_plan.get(node_id)}
    rule_units = {
        node_id for node_id, rows in row_plan.items()
        if rows and node_id not in rule_gates
    }
    log_info(
        _STEP, "rule_units",
        units=len(rule_units), gates=len(rule_gates),
        analysis_nodes=len(analysis_ids),
        obligation_rows=sum(len(rows) for rows in row_plan.values()),
        empty_units=sum(1 for rows in row_plan.values() if not rows),
    )

    # run 공유 슬롯 — 후처리와 같은 세마포어(spec 096). 각 concern은 독립 호출이므로
    # summary가 먼저 끝나는 즉시 후속 배치와 후처리에 공개한다.
    semaphore = handoff.slots
    handoff.expect(analysis_ids)
    # source_cache 는 caller(analyze_code_step)가 주입 — run 스코프 공유 (spec 046 FR-008).
    rule_graph_accumulator = RuleGraphResult()
    analyzed = rejected = batch_count = 0

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
        if caller in node_deps and callee in node_deps and caller != callee:
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
                            table_descriptions, source_cache, semaphore,
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
                    rule_graph = await finalize_and_emit_code_result(fn, stream)
                    _accumulate_rule_graph(rule_graph_accumulator, rule_graph)
                    analyzed += int(bool(fn.summary))
                    rejected += int(
                        _is_summary_terminal(fn, contexts[fn.id])
                        and not bool(fn.summary)
                    )
                # 인계 해제는 **공개 뒤에** — 순서가 뒤집히면 게이트가 열린 뒤에 노드가
                # 실려 후처리가 미완성 목록을 읽는다.
                for node_id in batch_nodes[id(b)]:
                    handoff.resolve(node_id)
    finally:
        # pause/stop/예산 차단/상위 취소 모두 coroutine finally까지 수거한 뒤 진행.
        await pause.cancel_and_wait(list(pending))

    # 모든 배치는 fail-closed다. trusted summary 또는 좌표·candidate를 보존한 명시적
    # 품질 거절만 terminal이다. 모델/배선 오류로 unavailable이 된 항목은 계속 missing이며
    # 정상 완료로 완화하지 않는다.
    unanalyzed = len(analysis_ids) - analyzed - rejected
    if unanalyzed:
        raise RuntimeError(
            f"DBMS 분석 불변식 위반: targets={len(analysis_ids)}, "
            f"summarized={analyzed}, rejected={rejected}, missing={unanalyzed}"
        )

    log_info(
        _STEP, "done",
        analysis_nodes=len(analysis_ids), batches=batch_count,
        analyzed=analyzed, rejected=rejected,
        in_cycle=len(dependency_plan.cycle_nodes),
        elapsed_sec=f"{time.monotonic() - started:.2f}",
    )
    return {
        "code_nodes": list(code_by_id.values()),
        "analysis_contexts": contexts,
        "rules": rule_graph_accumulator.rules,
        "has_rule_edges": rule_graph_accumulator.has_rule,
        "rule_table_edges": rule_graph_accumulator.table_edges,
    }


def _apply_effective_tree(
    contexts: dict[str, AnalysisContext],
    eff_parent: dict[str, str | None],
    eff_children: dict[str, list[str]],
    code_by_id: dict[str, CodeNode],
) -> None:
    """컨텍스트의 child_ids/parent_id 를 유효(skip-투명) 값으로 덮어쓴다.

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
    table_descriptions, source_cache, semaphore,
    rule_units: set[str], rule_gates: set[str], on_summaries_ready,
):
    """배치 1개 분석. 운영·결정론 실패만 상위 step까지 전파한다."""
    async with track_processing("Batch", batch.parent_id or batch.node_ids[0]):
        return await _analyze_batch(
            batch, contexts, code_by_id, strategy, cost_budget,
            table_descriptions, source_cache,
            semaphore, rule_units, rule_gates, on_summaries_ready,
        )


async def _analyze_batch(
    batch: Batch, contexts, code_by_id, strategy, cost_budget,
    table_descriptions, source_cache, llm_slots,
    rule_units, rule_gates, on_summaries_ready,
) -> list[CodeNode]:
    """배치 형제들을 관심사별로 한 번 분석하고 모델 품질 finding은 격리한다."""
    summary_prompts_dir = semantic_prompt_dir(
        family="summary", strategy=strategy.name,
    )
    table_prompts_dir = semantic_prompt_dir(
        family="table", strategy=strategy.name,
    )
    node_ids = list(batch.node_ids)

    pre_findings: dict[str, list[AnalysisFinding]] = {}
    rule_ids = [nid for nid in node_ids if nid in rule_units]
    gate_ids = [nid for nid in node_ids if nid in rule_gates]
    dml_ids = _direct_table_node_ids(node_ids, contexts)
    tasks = {}

    async def _summary_concern():
        try:
            items = await _collect_keyed_concern(
                parent_id=batch.parent_id,
                node_ids=node_ids,
                concern="summary",
                rule_name="code_summary_node",
                prompts_dir=summary_prompts_dir,
                contexts=contexts,
                code_by_id=code_by_id,
                cost_budget=cost_budget,
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
            if summary is not None:
                contradiction = _apply_batch_summary(
                    summary, code_by_id[node_id], contexts[node_id], strategy,
                    contexts,
                    evidence_text=str(
                        contexts[node_id].extra.get(
                            "summary_comparison_evidence", "",
                        )
                    ),
                )
                if contradiction is not None:
                    findings.setdefault(node_id, []).append(contradiction)
        on_summaries_ready(node_ids)
        return items, findings

    tasks["summary"] = _summary_concern()
    rule_materialization = _materialize_dbms_rules(
        [*rule_ids, *gate_ids],
        contexts=contexts,
        code_by_id=code_by_id,
        source_cache=source_cache,
    )
    if rule_materialization.items:
        tasks["rules"] = _collect_dbms_rule_meanings(
            rule_materialization,
            parent_id=batch.parent_id,
            contexts=contexts,
            strategy=strategy,
            cost_budget=cost_budget,
            table_descriptions=table_descriptions,
            source_cache=source_cache,
            llm_slots=llm_slots,
        )
    if dml_ids:
        async def _table_usage_meaning_concern():
            try:
                items = await _collect_keyed_concern(
                    parent_id=batch.parent_id,
                    node_ids=dml_ids,
                    concern="table_usage_meanings",
                    rule_name="code_table_usage_meanings_batch",
                    prompts_dir=table_prompts_dir,
                    contexts=contexts,
                    code_by_id=code_by_id,
                    cost_budget=cost_budget,
                    table_descriptions=table_descriptions,
                    source_cache=source_cache,
                    llm_slots=llm_slots,
                    strategy_name=strategy.name,
                )
                return items, {}
            except ModelOutputError as error:
                return {}, {
                    node_id: [AnalysisFinding(
                        concern="function_table_usage_meanings",
                        status="unavailable",
                        target_id=node_id,
                        reasons=(str(error),),
                        evidence_rule=error.rule_name,
                    )]
                    for node_id in dml_ids
                }

        tasks["table_usage_meanings"] = _table_usage_meaning_concern()

    results = await asyncio.gather(*tasks.values())
    done = dict(zip(tasks, results))

    summaries, summary_findings = done["summary"]
    for node_id, findings in summary_findings.items():
        pre_findings.setdefault(node_id, []).extend(findings)

    completed_rules = done.get("rules", rule_materialization)
    rules = completed_rules.items
    for node_id, node_findings in completed_rules.findings.items():
        pre_findings.setdefault(node_id, []).extend(node_findings)

    if "table_usage_meanings" in done:
        table_usages, table_usage_findings = done["table_usage_meanings"]
        for node_id, findings in table_usage_findings.items():
            pre_findings.setdefault(node_id, []).extend(findings)
    else:
        table_usages = {}
    applied: list[CodeNode] = []
    for fid in node_ids:
        requested: set[str] = set()
        if fid in rule_units or fid in rule_gates:
            requested.add("rules")
        if fid in dml_ids:
            requested.add("table_usage_meanings")
        applied.append(_apply_node_analysis(
            summaries.get(fid),
            rules.get(fid),
            table_usages.get(fid),
            code_by_id[fid],
            contexts[fid],
            strategy,
            source_cache,
            contexts=contexts,
            code_by_id=code_by_id,
            requested_parts=requested,
            pre_findings=pre_findings.get(fid, ()),
        ))
    return applied


def _direct_table_node_ids(node_ids, contexts) -> list[str]:
    """Return only nodes that directly own a READS/WRITES graph edge.

    DBMS routine contexts intentionally aggregate descendant R/W for summary and RULE
    validation. Table-usage meaning is a node-direct concern, so inherited scope must not schedule
    another model call or duplicate descendant table meaning.
    """
    return [
        node_id for node_id in node_ids
        if exact_direct_table_ids(contexts[node_id], "READ")
        or exact_direct_table_ids(contexts[node_id], "WRITE")
    ]


async def _collect_keyed_concern(
    *,
    parent_id,
    node_ids,
    concern,
    rule_name,
    prompts_dir,
    contexts,
    code_by_id,
    cost_budget,
    table_descriptions,
    source_cache,
    llm_slots,
    strategy_name,
) -> dict[str, Any]:
    """DBMS Summary 또는 keyed TABLE concern을 현재 계약대로 호출한다."""
    expected = _expected_keys(node_ids, contexts)
    if concern == "summary":
        async def _collect_one_summary(node_id: str) -> tuple[str, Any]:
            context = contexts[node_id]
            profile, _all_specs, concerns = context.extra["_semantic_frame_plan"]
            slot_specs = concerns["summary"]
            analysis_inputs = {
                "raw_source_code": exact_source_body(context, source_cache),
                "source_outline": render_source_outline(
                    code_by_id[node_id], code_by_id,
                ),
                "direct_table_usages": list(
                    function_table_usage_prompt_assignments(
                        profile,
                        concerns["table_usage_meanings"],
                        exact_direct_table_ids(context, "READ"),
                        exact_direct_table_ids(context, "WRITE"),
                    ).values()
                ),
                "referenced_symbols": _project_referenced_symbols(context),
                "external_source_text_by_id": {
                    item.id: item.text for item in context.external_evidence
                },
                "callee_effects_by_id": _direct_callee_effects_by_id(
                    context,
                    contexts,
                ),
            }
            inventory = build_control_inventory(
                code_by_id[node_id], context, source_cache,
            )
            context.extra["summary_comparison_evidence"] = "\n".join((
                analysis_inputs["raw_source_code"], inventory.branch_index(),
                code_by_id[node_id].expression or "",
            ))

            async def _invoke(summary_values, summary_model):
                queued_at = time.monotonic()
                waiting_before = llm_slots.waiting
                async with llm_slots.at(SUMMARY_WAKE_PRIORITY, SUMMARY_LANE):
                    payload_chars = sum(
                        len(str(value)) for value in summary_values.values()
                    )
                    queue_wait_ms = round(
                        (time.monotonic() - queued_at) * 1000, 3,
                    )
                    log_info(
                        _STEP,
                        "llm_slot_acquired",
                        concern="summary",
                        lane=SUMMARY_LANE,
                        parent_id=parent_id,
                        node_count=1,
                        node_id=node_id,
                        priority=SUMMARY_WAKE_PRIORITY,
                        payload_chars=payload_chars,
                        queue_wait_ms=queue_wait_ms,
                        waiting_before=waiting_before,
                        slots_in_use=llm_slots.in_use,
                        slots_capacity=llm_slots.capacity,
                    )
                    return await run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="code_summary_node",
                        user_values=summary_values,
                        response_model=summary_model,
                        budget=cost_budget,
                        request_metrics={
                            "concern": "summary",
                            "lane": SUMMARY_LANE,
                            "queue_wait_ms": queue_wait_ms,
                            "payload_chars": payload_chars,
                            "node_count": 1,
                            "input_sections": {
                                "source": len(summary_values["code_body"]),
                                "context": len(summary_values["summary_context"]),
                            },
                            "input_counts": _summary_input_counts(
                                profile, slot_specs, analysis_inputs,
                            ),
                            "waiting_before": waiting_before,
                            "slots_in_use": llm_slots.in_use,
                            "lane_slots_in_use": llm_slots.in_use_for(
                                SUMMARY_LANE
                            ),
                            "slots_capacity": llm_slots.capacity,
                        },
                    )

            return node_id, await _run_frame_summary(
                profile=profile, slot_specs=slot_specs,
                strategy_name=strategy_name, analysis_inputs=analysis_inputs,
                invoke=_invoke,
            )

        pending_ids = iter(node_ids)
        accepted: dict[str, Any] = {}

        async def _summary_worker() -> None:
            while True:
                try:
                    node_id = next(pending_ids)
                except StopIteration:
                    return
                stable_id, summary = await _collect_one_summary(node_id)
                accepted[stable_id] = summary

        tasks = [
            asyncio.create_task(_summary_worker())
            for _ in range(min(len(node_ids), llm_slots.capacity))
        ]
        try:
            await asyncio.gather(*tasks)
            return accepted
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    if concern != "table_usage_meanings":
        raise ValueError(f"unsupported DBMS concern: {concern!r}")
    table_usage_assignments = {}
    for node_key, node_id in expected.items():
        ctx = contexts[node_id]
        table_usage_assignments[node_key] = {
            "profile": ctx.extra["_semantic_frame_plan"][0],
            "slot_specs": ctx.extra["_semantic_frame_plan"][2][
                "table_usage_meanings"
            ],
            "reads": exact_direct_table_ids(ctx, "READ"),
            "writes": exact_direct_table_ids(ctx, "WRITE"),
        }
    effective_response_model = batch_function_table_usages_response_model(
        table_usage_assignments
    )
    values = build_table_usage_meaning_batch_values(
        batch=Batch(parent_id, tuple(node_ids)),
        contexts=contexts,
        code_by_id=code_by_id,
        table_descriptions=table_descriptions,
        cache=source_cache,
    )

    queue_priority = 0
    queued_at = time.monotonic()
    waiting_before = llm_slots.waiting
    payload_chars = sum(len(str(value)) for value in values.values())
    table_packets = json.loads(values["table_usage_packets"])
    table_counts = {
        "nodes": len(table_packets),
        "table_usages": 0,
        "occurrences": 0,
        "source_lines": 0,
    }
    for packet in table_packets.values():
        packet_counts = _table_input_counts(
            packet["assigned_table_usages"], packet["relevant_source"],
        )
        for key in ("table_usages", "occurrences", "source_lines"):
            table_counts[key] += packet_counts[key]
    queue_lane = TABLE_USAGE_MEANINGS_LANE
    async with llm_slots.at(queue_priority, queue_lane):
        queue_wait_ms = round((time.monotonic() - queued_at) * 1000, 3)
        log_info(
            _STEP,
            "llm_slot_acquired",
            concern=concern,
            lane=queue_lane,
            parent_id=parent_id,
            node_count=len(node_ids),
            priority=queue_priority,
            payload_chars=payload_chars,
            queue_wait_ms=queue_wait_ms,
            waiting_before=waiting_before,
            slots_in_use=llm_slots.in_use,
            slots_capacity=llm_slots.capacity,
        )
        output = await run_yaml_rule(
            prompts_dir=prompts_dir,
            rule_name=rule_name,
            user_values=values,
            response_model=effective_response_model,
            budget=cost_budget,
            request_metrics={
                "concern": concern,
                "lane": queue_lane,
                "queue_wait_ms": queue_wait_ms,
                "payload_chars": payload_chars,
                "node_count": len(node_ids),
                "input_sections": {"table_packets": payload_chars},
                "input_counts": table_counts,
                "waiting_before": waiting_before,
                "slots_in_use": llm_slots.in_use,
                "lane_slots_in_use": llm_slots.in_use_for(queue_lane),
                "slots_capacity": llm_slots.capacity,
            },
        )
    output = normalize_batch_function_table_usages_response(
        output, table_usage_assignments,
    )
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
    return batch_node_keys(node_ids)


def _materialize_dbms_rules(
    node_ids: list[str],
    *,
    contexts: Mapping[str, AnalysisContext],
    code_by_id: Mapping[str, CodeNode],
    source_cache: SourceFileCache,
) -> DbmsRuleMaterialization:
    """Materialize each DBMS node's assigned routine rows with zero model calls."""
    if not node_ids:
        return DbmsRuleMaterialization(items={}, findings={}, evidence={})
    expected = _expected_keys(node_ids, contexts)
    result: dict[str, BatchRulesItem] = {}
    findings: dict[str, list[AnalysisFinding]] = {}
    evidence: dict[str, dict[str, object]] = {}
    for node_key, node_id in expected.items():
        routine, inventory = routine_inventory(
            code_by_id[node_id], contexts, code_by_id, source_cache,
        )
        assigned = tuple(int(row) for row in (
            contexts[node_id].extra.get("rule_rows") or ()
        ))
        routine_ctx = contexts[routine.id]
        source = source_cache.slice(
            routine_ctx.file_path, routine_ctx.start_line, routine_ctx.end_line,
        )
        collection = materialize_and_validate_rules(
            inventory=inventory,
            target_scope=node_id,
            source=source,
            rows=assigned,
            allowed_tables=sorted(set(routine_ctx.reads) | set(routine_ctx.writes)),
            read_tables=sorted(set(routine_ctx.reads)),
            write_tables=sorted(set(routine_ctx.writes)),
            table_ops=routine_ctx.extra.get("table_op") or {},
            table_aliases=routine_ctx.extra.get("table_identity_aliases") or {},
            condition_bundle=routine.extra.get("_resolved_rule_condition_bundle"),
        )
        result[node_id] = BatchRulesItem(
            node_key=node_key,
            rules=collection.rules,
        )
        evidence[node_id] = collection.evidence
        if collection.findings:
            findings[node_id] = list(collection.findings)
    return DbmsRuleMaterialization(
        items=result,
        findings=findings,
        evidence=evidence,
    )


async def _collect_dbms_rule_meanings(
    materialization: DbmsRuleMaterialization,
    *,
    parent_id: str | None,
    contexts: Mapping[str, AnalysisContext],
    strategy: Any,
    cost_budget,
    table_descriptions: Mapping[str, Mapping[str, object]],
    source_cache: SourceFileCache,
    llm_slots,
) -> DbmsRuleMaterialization:
    """Fill all DBMS Rule atoms in one batch call; no judge or repair call."""
    node_ids = list(materialization.items)
    if not node_ids:
        return materialization
    key_to_id = batch_node_keys(node_ids)
    assignments = {
        node_key: prepare_rule_meaning_assignment(
            materialization.evidence[node_id]
        )
        for node_key, node_id in key_to_id.items()
    }
    active_partitions: dict[str, tuple[dict[str, object], ...]] = {}
    unavailable_keys: set[str] = set()
    for node_key, node_id in key_to_id.items():
        assignment = assignments[node_key]
        evidence = assignment.model_evidence
        rows = evidence.get("rows") or {}
        if not rows:
            continue
        conditions = evidence.get("conditions") or {}
        effects = evidence.get("effects") or {}
        if not conditions and not effects:
            unavailable_keys.add(node_key)
            continue
        active_partitions[node_key] = partition_rule_meaning_evidence(evidence)

    units: list[tuple[str, int, dict[str, object]]] = [
        (node_key, partition_index, partition)
        for node_key, partitions in active_partitions.items()
        for partition_index, partition in enumerate(partitions)
    ]
    request_groups: list[list[tuple[str, int, dict[str, object]]]] = []
    current_group: list[tuple[str, int, dict[str, object]]] = []
    current_effects = 0
    for unit in units:
        unit_effects = len(unit[2].get("effects") or {})
        if current_group and current_effects + unit_effects > RULE_MEANING_EFFECT_BUDGET:
            request_groups.append(current_group)
            current_group = []
            current_effects = 0
        current_group.append(unit)
        current_effects += unit_effects
    if current_group:
        request_groups.append(current_group)

    projected_by_partition: dict[tuple[str, int], tuple] = {}

    async def _run_dbms_rule_group(
        group: list[tuple[str, int, dict[str, object]]], group_index: int,
    ) -> None:
        local_assignments = {
            f"N{index}": partition
            for index, (_node_key, _partition_index, partition)
            in enumerate(group, start=1)
        }
        local_owners = {
            f"N{index}": (node_key, partition_index)
            for index, (node_key, partition_index, _partition)
            in enumerate(group, start=1)
        }
        packets = {}
        for local_key, partition in local_assignments.items():
            node_key, _partition_index = local_owners[local_key]
            node_id = key_to_id[node_key]
            context = contexts[node_id]
            packets[local_key] = build_rule_meaning_packet(
                partition,
                referenced_symbols=context.referenced_symbols,
                callee_effects_by_id=_direct_callee_effects_by_id(
                    context, dict(contexts),
                ),
                table_descriptions=table_descriptions,
                api_contracts=tuple(context.extra.get("api_contracts") or ()),
            )
        values = {
            "target_id": parent_id or key_to_id[group[0][0]],
            "rule_packets": json.dumps(
                packets,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        rule_counts = {
            "nodes": len(packets), "conditions": 0, "effects": 0,
            "rows": 0, "used_symbols": 0,
            "partition_index": group_index,
            "partition_count": len(request_groups),
        }
        for packet in packets.values():
            packet_counts = _rule_input_counts(packet)
            for key in ("conditions", "effects", "rows", "used_symbols"):
                rule_counts[key] += packet_counts[key]
        queued_at = time.monotonic()
        waiting_before = llm_slots.waiting
        try:
            async with llm_slots.at(0, RULES_LANE):
                queue_wait_ms = round((time.monotonic() - queued_at) * 1000, 3)
                log_info(
                    _STEP, "llm_slot_acquired", concern="rules", lane=RULES_LANE,
                    parent_id=parent_id, node_count=len(local_assignments),
                    payload_chars=len(values["rule_packets"]),
                    queue_wait_ms=queue_wait_ms, waiting_before=waiting_before,
                    slots_in_use=llm_slots.in_use, slots_capacity=llm_slots.capacity,
                )
                output = await run_yaml_rule(
                    prompts_dir=semantic_prompt_dir(family="rule", strategy="dbms"),
                    rule_name="code_rule_meanings_batch",
                    user_values=values,
                    response_model=batch_rule_meaning_response_model(local_assignments),
                    budget=cost_budget,
                    request_metrics={
                        "concern": "rules", "lane": RULES_LANE,
                        "queue_wait_ms": queue_wait_ms,
                        "payload_chars": len(values["rule_packets"]),
                        "node_count": len(local_assignments),
                        "input_sections": {"rule_packets": len(values["rule_packets"])},
                        "input_counts": rule_counts,
                        "waiting_before": waiting_before,
                        "slots_in_use": llm_slots.in_use,
                        "lane_slots_in_use": llm_slots.in_use_for(RULES_LANE),
                        "slots_capacity": llm_slots.capacity,
                    },
                )
            local_results = project_batch_rule_meanings(output, local_assignments)
            for local_key, projections in local_results.items():
                projected_by_partition[local_owners[local_key]] = projections
        except ModelOutputError as error:
            for local_key, partition in local_assignments.items():
                projected_by_partition[local_owners[local_key]] = (
                    unavailable_rule_meaning_projections(
                        partition, reason=str(error),
                    )
                )

    if request_groups:
        await asyncio.gather(*(
            _run_dbms_rule_group(group, index)
            for index, group in enumerate(request_groups, start=1)
        ))

    items: dict[str, BatchRulesItem] = {}
    findings: dict[str, list[AnalysisFinding]] = {}
    for node_key, node_id in key_to_id.items():
        item = materialization.items[node_id]
        collection = RowCollection(
            rules=list(item.rules),
            findings=list(materialization.findings.get(node_id, ())),
            evidence=materialization.evidence[node_id],
        )
        if node_key in unavailable_keys:
            assignment = assignments[node_key]
            collection = apply_rule_meaning_projections(
                collection,
                (
                    *unavailable_rule_meaning_projections(
                        assignment.model_evidence,
                        reason=(
                            "Rule row has no condition/effect atom from which to derive "
                            "business meaning"
                        ),
                    ),
                    *assignment.deterministic_projections,
                ),
                target_scope=node_id,
                count_call=False,
            )
        else:
            assignment = assignments[node_key]
            model_projections = tuple(
                projection
                for partition_index in range(len(active_partitions.get(node_key, ())))
                for projection in projected_by_partition.get(
                    (node_key, partition_index), ()
                )
            )
            collection = apply_rule_meaning_projections(
                collection,
                (
                    *model_projections,
                    *assignment.deterministic_projections,
                ),
                target_scope=node_id,
                count_call=len(active_partitions.get(node_key, ())),
            )
        items[node_id] = item.model_copy(update={"rules": collection.rules})
        if collection.findings:
            findings[node_id] = collection.findings
    return DbmsRuleMaterialization(
        items=items,
        findings=findings,
        evidence=materialization.evidence,
    )


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
    contexts: Mapping[str, AnalysisContext],
    *,
    evidence_text: str = "",
) -> AnalysisFinding | None:
    """Publish the summary-owned fields without waiting for sibling concerns."""
    frame = summary.semantic_frame
    if frame is None:
        raise ValueError(f"frame-first batch summary is missing semantic frame: {fn.id}")
    fn.extra["_summary_semantic_frame"] = frame
    ctx.extra["_summary_semantic_frame"] = frame
    if summary.evidence_status == "insufficient":
        fn.summary = None
        fn.stereotype = None
        ctx.summary = ""
        ctx.extra["summary_status"] = "needs_context"
        return AnalysisFinding(
            concern="summary",
            status="unverified",
            target_id=fn.id,
            reasons=tuple(summary.missing_context),
            candidate=summary.model_dump(),
            evidence_rule="code_summary_node",
        )
    summary_text = normalize_canonical_summary(summary.summary)
    copied_from = copied_callee_summary_id(
        summary_text,
        (
            (callee_id, contexts[callee_id].summary)
            for callee_id in ctx.callees
            if callee_id in contexts
        ),
    )
    if copied_from is not None:
        fn.summary = None
        fn.stereotype = None
        ctx.summary = ""
        # 거절 후보를 후속 의미 입력으로 전달하지 않는다.
        ctx.extra["summary_status"] = "rejected"
        _degrade_batch_summary_semantic_frame(
            fn, ctx, "validated summary unavailable: callee summary copy rejected",
        )
        return AnalysisFinding(
            concern="summary",
            status="rejected",
            target_id=fn.id,
            reasons=(
                "summary가 직접 callee canonical summary와 동일하여 "
                "현재 대상의 책임을 소유하지 않음",
            ),
            candidate={
                "summary": summary_text,
                "copied_callee_id": copied_from,
            },
            evidence_rule="code_summary_node",
        )
    missing_identities = missing_structural_summary_identities(summary_text, fn)
    if missing_identities:
        fn.summary = None
        fn.stereotype = None
        ctx.summary = ""
        ctx.extra["summary_status"] = "rejected"
        _degrade_batch_summary_semantic_frame(
            fn, ctx, "validated summary unavailable: direct AST identities omitted",
        )
        return AnalysisFinding(
            concern="summary",
            status="rejected",
            target_id=fn.id,
            reasons=(
                "summary omitted direct AST physical identities: "
                + ", ".join(missing_identities),
            ),
            candidate={
                "summary": summary_text,
                "missing_structural_identities": list(missing_identities),
            },
            evidence_rule="code_summary_node",
        )
    unsupported_comparisons = unsupported_code_comparison_claims(
        summary_text, evidence_text, ctx.referenced_symbols,
    )
    if unsupported_comparisons:
        fn.summary = None
        fn.stereotype = None
        ctx.summary = ""
        ctx.extra["summary_status"] = "rejected"
        _degrade_batch_summary_semantic_frame(
            fn, ctx, "validated summary unavailable: comparison operands changed",
        )
        return AnalysisFinding(
            concern="summary",
            status="rejected",
            target_id=fn.id,
            reasons=(
                "summary changed explicit comparison operands: "
                + "; ".join(unsupported_comparisons),
            ),
            candidate={
                "summary": summary_text,
                "unsupported_code_comparisons": list(unsupported_comparisons),
            },
            evidence_rule="code_summary_node",
        )
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
    ctx.summary = summary_text
    ctx.extra["summary_status"] = (
        "partial" if summary.evidence_status == "partial" else "accepted"
    )
    if summary.evidence_status == "partial":
        reasons = list(summary.missing_context)
        if violation:
            reasons.append(violation)
        return AnalysisFinding(
            concern="summary",
            status="unverified",
            target_id=fn.id,
            reasons=tuple(reasons),
            candidate=summary.model_dump(),
            evidence_rule="code_summary_node",
        )
    if not violation:
        return None
    return AnalysisFinding(
        concern="stereotype",
        status="rejected",
        target_id=fn.id,
        reasons=(violation,),
        candidate={"stereotype": summary.stereotype},
        evidence_rule="code_summary_node",
    )


def _degrade_batch_summary_semantic_frame(
    fn: CodeNode, ctx: AnalysisContext, reason: str,
) -> None:
    frame = fn.extra.get("_summary_semantic_frame")
    if frame is None:
        raise ValueError(f"summary semantic frame missing while rejecting output: {fn.id}")
    degraded = mark_semantic_frame_insufficient(frame, (reason,))
    fn.extra["_summary_semantic_frame"] = degraded
    ctx.extra["_summary_semantic_frame"] = degraded


def _apply_node_analysis(
    summary: Optional[BatchSummaryItem],
    rules: Optional[BatchRulesItem],
    table: Optional[BatchFunctionTableUsagesItem],
    fn: CodeNode,
    ctx: AnalysisContext,
    strategy: Any,
    source_cache: SourceFileCache,
    *,
    contexts: Mapping[str, AnalysisContext],
    code_by_id: Mapping[str, CodeNode],
    requested_parts: set[str],
    pre_findings=(),
) -> CodeNode:
    """summary/검증된 Rule meaning/table 결과를 CodeNode와 context에 반영한다.

    rules/table 은 해당 관심사를 요청하지 않은 노드에서만 None이다.
    """
    findings = list(pre_findings)
    if "table_usage_meanings" in requested_parts:
        function_table_usages = [
            item.model_dump() for item in table.function_table_usages
        ] if table else []
        if function_table_usages:
            fn.extra["function_table_usages"] = function_table_usages
        else:
            fn.extra.pop("function_table_usages", None)
        direct_column_evidence = dict(
            ctx.extra.get("column_usage_candidates") or {}
        )
        if direct_column_evidence:
            fn.extra["direct_column_usage_evidence_by_table"] = direct_column_evidence
        else:
            fn.extra.pop("direct_column_usage_evidence_by_table", None)
    if "rules" in requested_parts:
        # Rule candidates were already materialized and validated at the single common
        # producer boundary.  This application boundary only checks immutable accounting;
        # re-grading here previously turned an unverified row into a whole-run failure.
        routine = owning_routine(fn, code_by_id)
        routine_ctx = contexts[routine.id]
        assigned = set(ctx.extra.get("rule_rows") or ())
        if rules is None:
            raise ValueError(f"{fn.id}: deterministic Rule result is missing")
        accepted_rules = list(rules.rules)
        accepted_lines = [rule.anchor_line for rule in accepted_rules]
        accepted_rows = set(accepted_lines)
        rule_findings = [finding for finding in findings if finding.concern == "rules"]
        invalid_findings = [
            finding for finding in rule_findings
            if finding.status != "unverified"
        ]
        finding_lines = [
            row for finding in rule_findings for row in finding.coordinates
        ]
        finding_rows = set(finding_lines)
        malformed_findings = [
            finding for finding in rule_findings
            if len(finding.coordinates) != 1
            or finding.target_id
            != f"{fn.id}::R-{finding.coordinates[0]}"
        ]
        accounting_invalid = (
            len(accepted_lines) != len(accepted_rows)
            or len(finding_lines) != len(finding_rows)
            or bool(accepted_rows & finding_rows)
            or accepted_rows | finding_rows != assigned
        )
        if invalid_findings or malformed_findings or accounting_invalid:
            reasons = [
                reason
                for finding in (*invalid_findings, *malformed_findings)
                for reason in finding.reasons
            ]
            raise ValueError(
                f"{fn.id}: deterministic Rule invariant failed: "
                + "; ".join(reasons or ("assigned-row coverage mismatch",))
            )
        rule_finding_records = [
            finding.to_dict() for finding in rule_findings
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
            routine_ctx.reads,
            routine_ctx.writes,
            routine_ctx.extra.get("table_op"),
            routine_ctx.extra.get("table_identity_aliases") or {},
        )
        fn.extra["rules"] = serialized
        if unresolved_tables:
            raise ValueError(
                f"{fn.id}: then 테이블 효과가 결정론 R/W와 불일치: "
                f"{sorted(set(unresolved_tables))[:8]}"
            )
    try:
        _finalize_batch_semantic_frame(fn, ctx, table)
    except ValueError as error:
        findings.append(AnalysisFinding(
            concern="semantic_frame",
            status="rejected",
            target_id=fn.id,
            reasons=(str(error),),
            evidence_rule="semantic-frame/v1",
        ))
        fn.extra.pop("semantic_frame_artifact_hash", None)
        ctx.extra.pop("semantic_frame_artifact_hash", None)
    # ctx에 부착 — 부모의 결정론 규칙 검증과 의존 해제에서 라이브 조회.
    if "rules" in requested_parts:
        ctx.extra["rules"] = fn.extra["rules"]
    record_code_analysis_findings(fn, findings, component=_STEP)
    if fn.label in ROUTINE_LABELS:
        finalize_function_meaning(fn, findings)
    return fn


def _finalize_batch_semantic_frame(
    fn: CodeNode,
    ctx: AnalysisContext,
    table: BatchFunctionTableUsagesItem | None,
) -> None:
    plan = ctx.extra.get("_semantic_frame_plan")
    if plan is None:
        raise ValueError(f"semantic frame plan missing: {fn.id}")
    _profile, all_specs, _concerns = plan
    merged = merge_semantic_frames(
        fn.extra.get("_summary_semantic_frame"),
        table.semantic_frame if table is not None else None,
        required_slot_ids=(spec.slot_id for spec in all_specs),
    )
    artifact_hash = persist_function_semantic_frame(merged)
    fn.extra["semantic_frame_artifact_hash"] = artifact_hash
    ctx.extra["semantic_frame_artifact_hash"] = artifact_hash
