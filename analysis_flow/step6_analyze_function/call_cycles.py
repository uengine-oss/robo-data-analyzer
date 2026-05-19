"""순환 호출 식별 — Tarjan SCC 알고리즘 (iterative — 재귀 깊이 부작용 X).

원리: 함수 호출 그래프의 강하게 연결된 컴포넌트 (SCC) 가 곧 cycle.
- 크기 1 + 자기 호출 X = cycle 아님 (그냥 노드)
- 크기 1 + 자기 호출 = 1-cycle (recursive function)
- 크기 ≥ 2 = N-cycle (상호 호출)

결과: ``function_id`` → ``cycle_group_id`` mapping.
``analysis_order`` 가 이걸 받아 같은 그룹을 한 Round 로 묶음.

**이전 구현 vs 현재**:
- 이전: 재귀 strongconnect + ``sys.setrecursionlimit()`` — 전역 상태 변경 부작용.
- 현재: explicit stack 으로 iterative — 재귀 한도 무관, 매우 큰 그래프도 안전.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def find_call_cycles(
    edges: Iterable[tuple[str, str]],
    all_nodes: Iterable[str],
) -> dict[str, str]:
    """Tarjan SCC (iterative) 로 cycle 식별.

    Args:
        edges: ``(caller, callee)`` tuples.
        all_nodes: 전체 ``function_id``.

    Returns:
        ``function_id`` → ``cycle_group_id`` (cycle 안이면 그룹 id).
        cycle 밖 노드는 mapping 에서 빠짐 (호출자가 default = 자기 id 로 처리).

    Algorithm: Tarjan's strongly connected components — O(V + E), iterative.
    """
    graph: dict[str, list[str]] = defaultdict(list)
    nodes = set(all_nodes)
    for caller, callee in edges:
        if caller in nodes and callee in nodes:
            graph[caller].append(callee)

    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    scc_stack: list[str] = []
    sccs: list[list[str]] = []
    next_index = 0

    # 결정적 순회 — sorted (캐시 / 재현성 친화).
    for start in sorted(nodes):
        if start in indices:
            continue

        # iterative DFS — work_stack: (node, iterator over neighbors, child_done_flag).
        work_stack: list[tuple[str, iter, str | None]] = [
            (start, iter(graph.get(start, ())), None),
        ]
        indices[start] = next_index
        lowlinks[start] = next_index
        next_index += 1
        scc_stack.append(start)
        on_stack.add(start)

        while work_stack:
            v, neighbors, just_returned = work_stack[-1]

            if just_returned is not None:
                # 자식 DFS 끝나고 돌아옴 — lowlink 갱신
                lowlinks[v] = min(lowlinks[v], lowlinks[just_returned])
                work_stack[-1] = (v, neighbors, None)

            # 다음 neighbor 처리
            next_w = None
            for w in neighbors:
                if w not in indices:
                    next_w = w
                    break
                if w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])

            if next_w is not None:
                # 새 자식 진입
                indices[next_w] = next_index
                lowlinks[next_w] = next_index
                next_index += 1
                scc_stack.append(next_w)
                on_stack.add(next_w)
                work_stack[-1] = (v, neighbors, None)
                work_stack.append((next_w, iter(graph.get(next_w, ())), None))
                continue

            # 더 이상 자식 없음 — SCC root 면 추출
            if lowlinks[v] == indices[v]:
                scc: list[str] = []
                while True:
                    w = scc_stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)

            # pop + 부모에게 lowlink 전파
            work_stack.pop()
            if work_stack:
                parent_v, parent_neighbors, _ = work_stack[-1]
                work_stack[-1] = (parent_v, parent_neighbors, v)

    # cycle 만 추출 (size ≥ 2 또는 size 1 + self-loop)
    cycle_mapping: dict[str, str] = {}
    for scc in sccs:
        if len(scc) > 1 or (len(scc) == 1 and scc[0] in graph.get(scc[0], ())):
            group_id = f"__cycle__:{sorted(scc)[0]}"
            for fn in scc:
                cycle_mapping[fn] = group_id
    return cycle_mapping
