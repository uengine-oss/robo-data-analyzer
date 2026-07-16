"""Bottom-up DAG 분석 순서 결정 (leaf → root Round 별 정렬).

원리:
- leaf 코드노드 (callee 없음) 가 Round 1.
- Round 1 코드노드만 callee 로 가진 코드노드가 Round 2.
- 위로 올라가며 Round 증가.
- cycle 내부 코드노드는 ``call_cycles`` 로 식별 → 한 Round 에 모아 분석.

같은 Round 코드노드들은 병렬 분석 가능. Round 간은 순차 (caller 가 callee summary 필요).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from log.logger import log_warning


def bottom_up_rounds(
    edges: Iterable[tuple[str, str]],
    all_nodes: Iterable[str],
    cycles: dict[str, str] | None = None,
) -> list[list[str]]:
    """호출 그래프 → Bottom-up Round 별 정렬.

    Args:
        edges: ``(caller_id, callee_id)`` tuples.
               CALLS 와 PARENT_OF 모두 같은 형식 — 알고리즘 무관.
        all_nodes: 전체 ``code_id`` 집합 (callee 없는 leaf 도 포함).
        cycles: ``code_id`` → ``cycle_group_id`` mapping (``call_cycles`` 결과).
                같은 cycle_group 의 코드노드들은 한 SCC 로 묶어 같은 Round 에 배치.

    Returns:
        Round 리스트. 각 원소는 그 Round 의 code_id list (sorted 고정).
        ``Round[0]`` = leaf, ``Round[-1]`` = root.

    Note: cycle 내부 코드노드는 callee summary 없이 분석 (호출자가 ``is_in_cycle=True``
          메타 박음).
    """
    cycles = cycles or {}
    nodes = set(all_nodes)

    # cycle 묶음: cycle_group_id → 그 그룹의 코드노드 set
    # cycle 그룹은 SCC 단위로 한 가상 노드로 취급해 위상 정렬.
    group_of: dict[str, str] = {}    # code_id → group_id (cycle 또는 자기자신)
    for fn in nodes:
        group_of[fn] = cycles.get(fn, fn)

    group_members: dict[str, set[str]] = defaultdict(set)
    for fn, gid in group_of.items():
        group_members[gid].add(fn)

    # 그룹 단위 그래프 (cycle 내부 엣지는 제거 — 위상 정렬 가능하게)
    # 외부 callee (분석 영역 밖 — 프레임워크/런타임 코드노드 등) 는 의존성 아님 → 무시.
    # 무시 안 하면 children 카운트에 외부 노드가 박혀 pending 이 영원히 0 으로
    # 안 떨어짐 → 모든 코드노드 라운드 미배치 (call_cycles.py 와 동일 필터링 패턴).
    children: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, set[str]] = defaultdict(set)
    for caller, callee in edges:
        g_caller = group_of.get(caller)
        g_callee = group_of.get(callee)
        if g_caller is None or g_callee is None:
            continue  # 외부 노드 — 분석 영역 밖
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
        # 이번 Round 에 들어갈 code_id 들 (그룹 전개 + sorted 고정)
        members = sorted(fn for g in ready for fn in group_members[g])
        rounds.append(members)
        for g in ready:
            pending.pop(g)
            for p in parents[g]:
                if p in pending:
                    pending[p] -= 1

    # pending 잔여 = 위상 정렬에 안 들어간 그룹 — 라운드 미배치로 분석에서 누락된다.
    # SCC 가 정상이면 발생 X — 조용히 빠뜨리지 않게 표면화 (절대원칙 #3).
    if pending:
        leftover = sorted(fn for g in pending for fn in group_members[g])
        log_warning(
            "step6.analyze_code", "bottom_up_rounds",
            "위상 정렬 누락 함수 — 라운드 미배치로 분석에서 빠짐 (cycle 식별 누락 의심)",
            count=len(leftover), sample=leftover[:10],
        )
    return rounds
