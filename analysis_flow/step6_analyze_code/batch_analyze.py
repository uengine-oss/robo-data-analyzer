"""DBMS step6 배치 3분할 분석 (🅕) — 같은 유효 부모 형제 그룹을 관심사별 콜로 분석.

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

from analysis_flow.step6_analyze_code.analysis_order import bottom_up_rounds
from analysis_flow.step6_analyze_code.batch_planner import (
    Batch, build_effective_tree, plan_batches,
)
from analysis_flow.step6_analyze_code.call_cycles import find_call_cycles
from analysis_flow.step6_analyze_code.range_body import ancestor_context, body_for, skeleton_body
from analysis_flow.step6_analyze_code.rw_table_filter import constrain_tables_to_rw
from analysis_flow.step4_linking.stage2_extract_calls.call_linker import routine_call_pairs
from data_models.analysis_context import AnalysisContext
from data_models.code_analysis_output import (
    BatchRulesItem,
    BatchRulesOutput,
    BatchSummaryItem,
    BatchTableInsightsItem,
    BatchTableInsightsOutput,
    BatchSummaryOutput,
)
from data_models.graph_nodes import CodeNode
from shared.config.env_catalog import ENV
from shared.lang.ontology import coerce_stereotype
from shared.llm.cost_budget import BudgetExceededError
from shared.llm.yaml_llm_runner import run_yaml_rule
from shared.log.logger import log_error, log_info, log_warning
from shared.log.processing_progress import track_processing
from shared.source_file_cache import SourceFileCache
from shared.workflow.pause_controller import PauseController

_STEP = "step6.analyze_code.batch"

# rules(비즈니스 규칙) 추출 단위 = "원문(RAW)이 토큰 예산에 들어가는 가장 높은 문장"(budget descent).
# 루틴 직속(레벨1)부터 시작해 예산 초과 컨테이너만 자식으로 내려간다 — 증거는 항상 1차(원문),
# 크기는 예산으로 안전. 단위 아래 노드는 규칙 X(단위가 흡수 = 파편·중복 차단은 레벨1 원리 유지).
_ROUTINE_TYPES = frozenset({"PROCEDURE", "FUNCTION", "TRIGGER"})


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
        if node.label in _ROUTINE_TYPES:
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
    table_descriptions: dict[str, str],
    pause: PauseController,
) -> dict:
    """DBMS 배치 분석 — 유효 트리 → 바텀업 라운드 → 라운드별 형제 배치 1콜.

    결과는 ``analyze_code_step`` 과 동일한 state dict (analysis_contexts + EM 9종).

    일시정지/중단은 ``pause.gate()`` 로 소비 — Framework ``_run_rounds`` 와 동일 정책:
    paused 면 hold, stop 이면 RunStopped, 일시정지로 취소된 배치는 재개 시 재투입.
    """
    # persist/EM 헬퍼는 analyze_code_step 공유 (지연 import — 순환 회피).
    from analysis_flow.step6_analyze_code.analyze_code_step import (
        _accumulate_em, _persist_and_emit_code,
    )
    from shared.example_mapping.builder import ExampleMappingResult

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
    rule_units, rule_gates = _plan_rule_units(code_by_id, eff_children, token_of, ENV.pipeline.batch_max_tokens)
    log_info(_STEP, "rule_units", units=len(rule_units), gates=len(rule_gates), analysis_nodes=len(analysis_ids))

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
                            b, contexts, code_by_id, strategy, cost_budget,
                            codebase_notes, module_intents, table_descriptions, source_cache, semaphore,
                            rule_units, rule_gates,
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
                    for fn in result:              # 배치가 분석한 CodeNode 들
                        em = await _persist_and_emit_code(fn, neo, stream, source_cache)
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
    # spec 027: LLM 귀속 테이블을 결정론 reads/writes 로 제한 (callee 함수명 오귀속 차단).
    constrain_tables_to_rw(fn.id, ctx.reads, ctx.writes, table, rules)
    fn.extra["table_insights"] = [t.model_dump() for t in table.table_insights] if table else []
    fn.extra["rules"] = [r.model_dump() for r in rules.rules] if rules else []   # examples 는 각 rule 하위 중첩
    fn.extra["questions"] = [q.model_dump() for q in rules.questions] if rules else []
    # ctx 에 부착 — 부모 배치가 compact_body·자식규칙으로 라이브 조회.
    ctx.summary = summary.summary
    ctx.extra["rules"] = fn.extra["rules"]
    ctx.extra["questions"] = fn.extra["questions"]
    return fn


def _build_batch_values(
    batch: Batch,
    contexts: dict[str, AnalysisContext],
    code_by_id: dict[str, CodeNode],
    codebase_notes: str,
    module_intents: dict[str, str],
    table_descriptions: dict[str, str],
    cache: SourceFileCache,
    body_fn=body_for,
) -> tuple[dict, dict[str, str]]:
    """배치 → batch yaml user_values + ``node_key -> code_id`` 매핑.

    프로시저 정보(루트)·조상 골격은 배치 1회 공유(prefix 캐시). 각 형제는 줄범위 key +
    reads/writes/참조심볼 + body. body 는 ``body_fn`` — 기본 ``body_for``(예산 내 원문,
    초과 시 compact). 게이트 규칙 콜만 ``skeleton_body``(자식 접힘 — 자기 줄 조건만).
    """
    first_ctx = contexts[batch.node_ids[0]]
    root = _root_code_node(batch.node_ids[0], contexts, code_by_id)
    parent_ctx = contexts.get(batch.parent_id) if batch.parent_id else None
    ancestor = ancestor_context(first_ctx, contexts, cache) if parent_ctx is not None else "(없음)"

    blocks: list[str] = []
    key_to_id: dict[str, str] = {}
    for nid in batch.node_ids:
        ctx = contexts[nid]
        key = f"{ctx.start_line}-{ctx.end_line}"
        key_to_id[key] = nid
        body = body_fn(ctx, contexts, cache)
        blocks.append(
            f"### 노드 {key}\n"
            f"reads: {_fmt_tables(ctx.reads, table_descriptions)}\n"
            f"writes: {_fmt_tables(ctx.writes, table_descriptions)}\n"
            f"참조심볼: {', '.join(ctx.referenced_symbols) or '(없음)'}\n"
            f"body:\n```\n{body}\n```"
        )

    values = {
        "unit_id": root.id if root else first_ctx.id,
        "unit_signature": root.signature if root else first_ctx.signature,
        "module_intent": module_intents.get(root.owner_id if root else "") or "(없음)",
        "codebase_notes": codebase_notes or "(없음)",
        "ancestor_context": ancestor,
        "node_count": str(len(batch.node_ids)),
        "node_keys": ", ".join(key_to_id),
        "nodes": "\n\n".join(blocks),
    }
    return values, key_to_id


def _root_code_node(
    node_id: str, contexts: dict[str, AnalysisContext], code_by_id: dict[str, CodeNode],
) -> Optional[CodeNode]:
    """노드의 프로시저 루트 CodeNode — 유효 parent_id 체인 최상위."""
    cur = node_id
    seen: set[str] = set()
    while cur not in seen:
        seen.add(cur)
        ctx = contexts.get(cur)
        pid = ctx.extra.get("parent_id") if ctx else None
        if pid is None:
            break
        cur = pid
    return code_by_id.get(cur)


def _fmt_tables(fqns: tuple[str, ...], table_descriptions: dict[str, str]) -> str:
    """테이블 fqn 목록 → 한 줄 (설명 있으면 부기). 빈 목록은 '(없음)'."""
    if not fqns:
        return "(없음)"
    parts = []
    for fqn in fqns:
        desc = table_descriptions.get(fqn, "")
        parts.append(f"{fqn}({desc})" if desc else fqn)
    return ", ".join(parts)
