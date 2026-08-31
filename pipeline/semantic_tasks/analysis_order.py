"""Prompt summary 의존 계획과 leaf → root 분석 순서.

두 step7 엔진은 prompt를 실제로 받는 ``AnalysisContext``의 ``callees``와
구조적 parent→child 의존을 이 모듈에서 한 번만 합친다. 결합 SCC에서는 선행
제공이 불가능한 call-summary 엣지만 억제하고, 구조적 child-summary 순서는
보존한다. 그 결과인 비순환 ``schedule_edges``만 bottom-up 정렬한다.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from pipeline.models.analysis_context import AnalysisContext
from shared.graphs.call_cycles import find_call_cycles
from shared.events.runtime_logger import log_warning


@dataclass(frozen=True)
class SummaryDependencyPlan:
    """One deterministic contract shared by both step7 analysis engines.

    ``call_edges`` comes only from the prompt receiver's ``ctx.callees``.
    ``structural_edges`` carries child-summary handoff.  A summary dependency SCC
    means no member can provide its callee summary first, so only call edges inside
    that SCC are suppressed; structural child-before-parent ordering remains.  The
    suppressed caller/callee IDs are also attached to their contexts as explicit
    unavailable semantic dependencies before either engine builds a prompt.
    """

    call_edges: tuple[tuple[str, str], ...]
    structural_edges: tuple[tuple[str, str], ...]
    schedule_edges: tuple[tuple[str, str], ...]
    suppressed_call_edges: tuple[tuple[str, str], ...]
    cycle_nodes: frozenset[str]


def plan_summary_dependencies(
    contexts: Mapping[str, AnalysisContext],
    structural_edges: Iterable[tuple[str, str]],
) -> SummaryDependencyPlan:
    """Build the exact dependency graph consumed by summary scheduling.

    The context mapping is the prompt-input contract, so a call target outside it
    (external or not analyzed) is not a summary dependency.  Cyclic call summaries
    are unavailable by definition.  Removing structural edges to break those cycles
    would let a parent run before its child and can make compact-body assembly read a
    missing summary, therefore only same-SCC call edges are removed.  This planner
    also owns the deterministic cycle/unavailable annotations consumed by the semantic
    frame planner; callers must not reconstruct them from prose or scheduler timing.
    """
    nodes = set(contexts)
    mismatches = sorted(
        (key, ctx.id) for key, ctx in contexts.items() if key != ctx.id
    )
    if mismatches:
        raise ValueError(f"context key/id mismatch: {mismatches[:10]}")

    normalized_structural = tuple(sorted({
        (parent, child)
        for parent, child in structural_edges
        if parent in nodes and child in nodes
    }))
    call_edges = tuple(sorted({
        (context_id, callee_id)
        for context_id, ctx in contexts.items()
        for callee_id in ctx.callees
        if callee_id in nodes
    }))

    combined = (*normalized_structural, *call_edges)
    cycle_groups = find_call_cycles(combined, nodes)
    suppressed = tuple(
        edge for edge in call_edges
        if edge[0] in cycle_groups
        and cycle_groups.get(edge[0]) == cycle_groups.get(edge[1])
    )
    suppressed_set = set(suppressed)
    schedule_edges = tuple(sorted({
        *normalized_structural,
        *(edge for edge in call_edges if edge not in suppressed_set),
    }))

    remaining_cycles = find_call_cycles(schedule_edges, nodes)
    if remaining_cycles:
        raise ValueError(
            "structural summary dependency cycle remains after suppressing "
            f"cyclic call edges: {sorted(remaining_cycles)[:10]}"
        )

    unavailable_by_caller: dict[str, list[str]] = defaultdict(list)
    for caller_id, callee_id in suppressed:
        unavailable_by_caller[caller_id].append(callee_id)
    for context_id, context in contexts.items():
        context.is_in_cycle = context_id in cycle_groups
        unavailable = tuple(sorted(set(unavailable_by_caller.get(context_id, ()))))
        if unavailable:
            context.extra["unavailable_callee_dependencies"] = unavailable
        else:
            context.extra.pop("unavailable_callee_dependencies", None)

    return SummaryDependencyPlan(
        call_edges=call_edges,
        structural_edges=normalized_structural,
        schedule_edges=schedule_edges,
        suppressed_call_edges=suppressed,
        cycle_nodes=frozenset(cycle_groups),
    )


def bottom_up_rounds(
    edges: Iterable[tuple[str, str]],
    all_nodes: Iterable[str],
) -> list[list[str]]:
    """이미 계획된 비순환 summary 의존을 Bottom-up Round로 정렬.

    Args:
        edges: ``(caller_id, callee_id)`` tuples.
               CALLS 와 PARENT_OF 모두 같은 형식 — 알고리즘 무관.
        all_nodes: 전체 ``code_id`` 집합 (callee 없는 leaf 도 포함).
    Returns:
        Round 리스트. 각 원소는 그 Round 의 code_id list (sorted 고정).
        ``Round[0]`` = leaf, ``Round[-1]`` = root.
    """
    nodes = set(all_nodes)

    # 외부 callee (분석 영역 밖 — 프레임워크/런타임 코드노드 등) 는 의존성 아님 → 무시.
    # plan_summary_dependencies 사용자는 이미 필터링되지만 함수 자체도 안전하게 유지한다.
    children: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, set[str]] = defaultdict(set)
    for caller, callee in edges:
        if caller not in nodes or callee not in nodes:
            continue  # 외부 노드 — 분석 영역 밖
        children[caller].add(callee)
        parents[callee].add(caller)

    # Kahn 알고리즘 — leaf 부터 Round 별 추출
    pending = {node: len(children[node]) for node in nodes}
    rounds: list[list[str]] = []
    while True:
        ready = sorted(node for node, count in pending.items() if count == 0)
        if not ready:
            break
        rounds.append(ready)
        for node in ready:
            pending.pop(node)
            for p in parents[node]:
                if p in pending:
                    pending[p] -= 1

    # pending 잔여 = 계획되지 않은 cycle. 조용히 분석에서 누락시키지 않는다.
    if pending:
        leftover = sorted(pending)
        log_warning(
            "step7.analyze_code", "bottom_up_rounds",
            "위상 정렬 누락 함수 — summary dependency 계획에서 cycle 억제 누락 의심",
            count=len(leftover), sample=leftover[:10],
        )
    return rounds
