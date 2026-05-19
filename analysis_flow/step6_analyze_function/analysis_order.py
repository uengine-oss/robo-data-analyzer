"""Bottom-up DAG 분석 순서 결정 (leaf → root Round 별 정렬).

원리:
- leaf 함수 (callee 없음) 가 Round 1.
- Round 1 함수만 callee 로 가진 함수가 Round 2.
- 위로 올라가며 Round 증가.
- cycle 내부 함수는 ``call_cycles`` 로 식별 → 한 Round 에 모아 분석.

같은 Round 함수들은 병렬 분석 가능. Round 간은 순차 (caller 가 callee summary 필요).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def bottom_up_rounds(
    edges: Iterable[tuple[str, str]],
    all_nodes: Iterable[str],
    cycles: dict[str, str] | None = None,
) -> list[list[str]]:
    """호출 그래프 → Bottom-up Round 별 정렬.

    Args:
        edges: ``(caller_function_id, callee_function_id)`` tuples.
               CALLS 와 PARENT_OF 모두 같은 형식 — 알고리즘 무관.
        all_nodes: 전체 ``function_id`` 집합 (callee 없는 leaf 도 포함).
        cycles: ``function_id`` → ``cycle_group_id`` mapping (``call_cycles`` 결과).
                같은 cycle_group 의 함수들은 한 SCC 로 묶어 같은 Round 에 배치.

    Returns:
        Round 리스트. 각 원소는 그 Round 의 function_id list (sorted 고정).
        ``Round[0]`` = leaf, ``Round[-1]`` = root.

    Note: cycle 내부 함수는 callee summary 없이 분석 (호출자가 ``is_in_cycle=True``
          메타 박음).
    """
    cycles = cycles or {}
    nodes = set(all_nodes)

    # cycle 묶음: cycle_group_id → 그 그룹의 함수 set
    # cycle 그룹은 SCC 단위로 한 가상 노드로 취급해 위상 정렬.
    group_of: dict[str, str] = {}    # function_id → group_id (cycle 또는 자기자신)
    for fn in nodes:
        group_of[fn] = cycles.get(fn, fn)

    group_members: dict[str, set[str]] = defaultdict(set)
    for fn, gid in group_of.items():
        group_members[gid].add(fn)

    # 그룹 단위 그래프 (cycle 내부 엣지는 제거 — 위상 정렬 가능하게)
    children: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, set[str]] = defaultdict(set)
    for caller, callee in edges:
        g_caller = group_of.get(caller, caller)
        g_callee = group_of.get(callee, callee)
        if g_caller == g_callee:
            continue  # cycle 내부 — 같은 그룹
        children[g_caller].add(g_callee)
        parents[g_callee].add(g_caller)

    # Kahn 알고리즘 — leaf 부터 Round 별 추출
    pending = {g: len(children[g]) for g in group_members}
    rounds: list[list[str]] = []
    while True:
        ready = sorted(g for g, n in pending.items() if n == 0)
        if not ready:
            break
        # 이번 Round 에 들어갈 function_id 들 (그룹 전개 + sorted 고정)
        members = sorted(fn for g in ready for fn in group_members[g])
        rounds.append(members)
        for g in ready:
            pending.pop(g)
            for p in parents[g]:
                if p in pending:
                    pending[p] -= 1
    return rounds
