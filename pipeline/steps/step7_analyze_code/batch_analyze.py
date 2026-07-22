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
from pipeline.steps.step7_analyze_code.call_cycles import find_call_cycles
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
    BatchSummaryOutput,
)
from graph.schema.graph_nodes import CodeNode
from shared.config.settings import SETTINGS
from pipeline.steps.step7_analyze_code.domain_terms import normalize_domain_terms
from pipeline.steps.step7_analyze_code.summary_quality import enforce_summary_boundary
from pipeline.languages import ROUTINE_LABELS, coerce_stereotype
from llm.usage.cost_budget import BudgetExceededError
from llm.runners.yaml_llm_runner import run_yaml_rule
from shared.events.runtime_logger import log_error, log_info, log_warning
from shared.events.processing_events import track_processing
from shared.files.cache import SourceFileCache
from pipeline.execution.pause_controller import PauseController

_STEP = "step7.analyze_code.batch"

# rules(비즈니스 규칙) 추출 단위 = "원문(RAW)이 토큰 예산에 들어가는 가장 높은 문장"(budget descent).
# 루틴 직속(레벨1)부터 시작해 예산 초과 컨테이너만 자식으로 내려간다 — 증거는 항상 1차(원문),
# 크기는 예산으로 안전. 단위 아래 노드는 규칙 X(단위가 흡수 = 파편·중복 차단은 레벨1 원리 유지).
#
# 루틴 판정은 ``ontology.ROUTINE_LABELS`` 단일 정의처를 쓴다 (옛 로컬 ``_ROUTINE_TYPES`` 는
# METHOD 가 빠져 있어 실제로 갈라져 있었다 — spec 046 FR-005).


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

    semaphore = asyncio.Semaphore(SETTINGS.analysis_pipeline.max_concurrency)
    # source_cache 는 caller(analyze_code_step)가 주입 — run 스코프 공유 (spec 046 FR-008).
    em_acc = ExampleMappingResult()
    analyzed = failed = batch_count = 0

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
                    continue
                for fn in result:                  # 배치가 분석한 CodeNode 들
                    em = await _persist_and_emit_code(fn, neo, stream)
                    _accumulate_em(em_acc, em)
                    analyzed += 1
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
        analyzed=analyzed, failed_batches=failed or None,
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
    """배치 형제들을 1 LLM 콜로 분석 → node_key 분배. 누락 노드는 1회 미니 재요청 (누락 0 보장)."""
    applied, missing = await _batch_call(
        batch.parent_id, list(batch.node_ids), contexts, code_by_id, strategy,
        cost_budget, codebase_notes, module_intents, table_descriptions, source_cache,
        rule_units, rule_gates,
    )
    if missing:
        # 미니 재요청 — 누락 노드만 다시 1콜 (단독 배치라 적중률 ↑). silent fail 금지.
        retry, still = await _batch_call(
            batch.parent_id, missing, contexts, code_by_id, strategy,
            cost_budget, codebase_notes, module_intents, table_descriptions, source_cache,
            rule_units, rule_gates,
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
    parent_id, node_ids, contexts, code_by_id, strategy, cost_budget,
    codebase_notes, module_intents, table_descriptions, source_cache, rule_units, rule_gates,
) -> tuple[list[CodeNode], list[str]]:
    """그룹(node_ids)을 3 관심사 콜로 분석 → ``(적용된 CodeNode 들, 누락 node_id 들)``.

    ①요약(전 노드) ②비즈로직(rule_units=원문 / rule_gates=골격 — 게이트 조건만) ③테이블(DML 노드만).
    각 콜은 "한 관심사 × 노드 리스트" 평탄 출력 → 깊은 중첩 0(폭주 차단). node_key(줄범위)로 fan-in.
    흐름: 요약 먼저(같은 배치 입력 prefix 를 cache 에 올림) → 비즈로직+테이블 동시(적중).
    부분 성공 허용 — 비즈로직/테이블 한 조각 실패가 노드 폐기로 이어지지 않게(요약만 코어).
    """
    prompts_dir = Path(strategy.prompts_dir)
    tmp = Batch(parent_id, tuple(node_ids))
    user_values, key_to_id = _build_batch_values(
        tmp, contexts, code_by_id, codebase_notes,
        module_intents, table_descriptions, source_cache,
    )

    # 분할 1 요약 — 먼저(코어 + cache 워밍). 실패하면 배치 전체 누락 처리(미니 재요청).
    summary_out: BatchSummaryOutput = await run_yaml_rule(
        prompts_dir=prompts_dir, rule_name="code_summary_batch",
        user_values=user_values, response_model=BatchSummaryOutput, budget=cost_budget,
    )
    for summary in summary_out.nodes:
        fid = key_to_id.get(summary.node_key.strip())
        if fid is None:
            continue
        summary.summary = await enforce_summary_boundary(
            summary=summary.summary,
            node_name=code_by_id[fid].name,
            logical_name=summary.logical_name,
            search_text=summary.search_text,
            budget=cost_budget,
            audit_id=fid,
        )

    # 분할 3 비즈로직 — rule_units(예산 내 최고 조상) 노드만. 단위는 예산에 들어가므로
    #   body_for 가 이미 원문(자식 서브트리 포함)을 줬다 = 1차 증거, 특수 입력 빌드 불필요.
    #   단위 아래 노드는 규칙 X(단위가 흡수 — 파편·중복 차단). 배치에 단위가 일부만 있으면
    #   그 부분집합으로만 rules 콜(같은 빌더 재사용).
    # 분할 2 테이블 — DML 노드만.
    rule_ids = [nid for nid in node_ids if nid in rule_units]
    gate_ids = [nid for nid in node_ids if nid in rule_gates]
    dml_ids = [nid for nid in node_ids if contexts[nid].reads or contexts[nid].writes]
    tasks = {}
    if rule_ids:
        rule_values = user_values
        if len(rule_ids) != len(node_ids):
            rule_values, _ = _build_batch_values(
                Batch(parent_id, tuple(rule_ids)), contexts, code_by_id,
                codebase_notes, module_intents, table_descriptions, source_cache,
            )
        tasks["rules"] = run_yaml_rule(
            prompts_dir=prompts_dir, rule_name="code_rules_examples_batch",
            user_values=rule_values, response_model=BatchRulesOutput, budget=cost_budget,
        )
    if gate_ids:
        # 하강한 컨테이너 — 골격(자식 `....` 접힘)으로 자기 줄 게이트·선별 조건만 규칙화.
        # 자식 내용은 각 단위가 담당하므로 골격엔 안 보임 = 내용 중복 원천 차단.
        gate_values, _ = _build_batch_values(
            Batch(parent_id, tuple(gate_ids)), contexts, code_by_id,
            codebase_notes, module_intents, table_descriptions, source_cache,
            body_fn=skeleton_body,
        )
        tasks["gate_rules"] = run_yaml_rule(
            prompts_dir=prompts_dir, rule_name="code_gate_rules_batch",
            user_values=gate_values, response_model=BatchRulesOutput, budget=cost_budget,
        )
    if dml_ids:
        dml_values, _ = _build_batch_values(
            Batch(parent_id, tuple(dml_ids)), contexts, code_by_id,
            codebase_notes, module_intents, table_descriptions, source_cache,
        )
        tasks["table"] = run_yaml_rule(
            prompts_dir=prompts_dir, rule_name="code_table_insights_batch",
            user_values=dml_values, response_model=BatchTableInsightsOutput, budget=cost_budget,
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    done = dict(zip(tasks, results))
    for r in results:
        if isinstance(r, BudgetExceededError):
            raise r        # 회로 차단기 — 즉시 전파 (절대원칙 #3)

    summary_by_key = {s.node_key.strip(): s for s in summary_out.nodes}
    rules_by_key = {**_by_key(done.get("rules")), **_by_key(done.get("gate_rules"))}  # 단위∩게이트 = ∅
    table_by_key = _by_key(done.get("table"))

    applied: list[CodeNode] = []
    missing: list[str] = []
    for key, fid in key_to_id.items():
        s = summary_by_key.get(key)
        if s is None:                 # 요약(코어) 없음 = 누락 → 호출자가 미니 재요청
            missing.append(fid)
            continue
        applied.append(_apply_node_analysis(
            s, rules_by_key.get(key), table_by_key.get(key),
            code_by_id[fid], contexts[fid], strategy,
        ))
    return applied, missing


def _by_key(result) -> dict:
    """배치 분할 결과 → ``{node_key: item}``. 조각 실패/None 은 빈 dict(부분 성공 — 빈 결과 진행).

    호출 안 한 분할(테이블 DML 0)은 None → 빈 dict(정상). 호출했는데 실패면 log_warning.
    """
    if result is None:
        return {}
    if isinstance(result, BaseException):
        log_warning(
            _STEP, "split_failed", "배치 분할 조각 실패 — 빈 결과로 진행",
            error=f"{type(result).__name__}: {result}",
        )
        return {}
    return {item.node_key.strip(): item for item in result.nodes}


def _apply_node_analysis(
    summary: BatchSummaryItem,
    rules: Optional[BatchRulesItem],
    table: Optional[BatchTableInsightsItem],
    fn: CodeNode,
    ctx: AnalysisContext,
    strategy: Any,
) -> CodeNode:
    """3 배치 분할 결과(요약/비즈로직/테이블) → CodeNode + ctx 반영 (three_split fan-in 동일).

    rules/table 은 조각 실패·비-DML 이면 None → 빈 배열(부분 성공 허용, 요약만 코어).
    """
    fn.summary = summary.summary
    fn.stereotype = coerce_stereotype(summary.stereotype or "", strategy.name) or None
    fn.logical_name = summary.logical_name or None
    fn.search_text = summary.search_text or None
    fn.domain_terms = normalize_domain_terms(
        summary.domain_terms,
        source_text="\n".join(filter(None, (fn.comment, fn.code_text))),
        code_id=fn.id,
        component=_STEP,
    )
    # spec 027: LLM 귀속 테이블을 결정론 reads/writes 로 제한 (callee 함수명 오귀속 차단).
    constrain_tables_to_rw(
        fn.id, ctx.reads, ctx.writes, table, rules,
        allowed_columns_by_table=ctx.extra.get("ddl_columns_by_table"),
    )
    fn.extra["table_insights"] = [t.model_dump() for t in table.table_insights] if table else []
    fn.extra["rules"] = [r.model_dump() for r in rules.rules] if rules else []   # examples 는 각 rule 하위 중첩
    fn.extra["questions"] = [q.model_dump() for q in rules.questions] if rules else []
    # ctx 에 부착 — 부모 배치가 compact_body·자식규칙으로 라이브 조회.
    ctx.summary = summary.summary
    ctx.extra["rules"] = fn.extra["rules"]
    ctx.extra["questions"] = fn.extra["questions"]
    return fn
