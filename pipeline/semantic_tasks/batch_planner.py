"""DBMS step7 배치 플래너 (🅕) — skip-투명 유효 트리 + 같은 부모 형제 토큰버짓 배치.

per-node(노드당 LLM 2~3콜) → 배치(같은 유효 부모 형제 N 노드를 1콜). 검증 알고리즘
(cAST split-then-merge · 형제 일관 청킹):

- **skip 노드**(괄호 `DECLARE`/`TRY`/`TRIGGER_BLOCK`)는 LLM 분석 제외하되 그래프엔 유지(E)
  → 컨텍스트에서 **투명**(자식이 부모로 bubble). 판별 = code_id `:TYPE:` 부분문자열
  (#2 CALL `:CALL:` 와 같은 메커니즘 — uniqueness 접미사에도 robust).
- **유효 트리** = parent_of 에서 skip 노드를 건너뛴 부모-자식. skip 의 자식은 가장 가까운
  non-skip 조상에 붙는다.
- **배치** = 같은 유효 부모 형제를 토큰버짓까지 greedy 병합. 루트(유효부모 None)는 서로
  다른 모듈이므로 각자 solo 배치(reduce 단위).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graph.schema.graph_nodes import CodeNode

# 그래프엔 그리되 LLM 분석 제외하는 순수 괄호 노드 (E — 사용자 확정 2026-06-05).
# 판별은 ``CodeNode.own_ast_type()`` (자기 타입 — 자손 오염 X). code_id 부분문자열 X.
_SKIP_ANALYSIS_TYPES = frozenset({"DECLARE", "TRY", "TRIGGER_BLOCK"})


@dataclass(frozen=True)
class Batch:
    """같은 유효 부모 형제 노드 묶음 — 1 LLM 콜 단위."""
    parent_id: str | None              # 유효 부모 (루트면 None)
    node_ids: tuple[str, ...]          # 이 배치의 노드들 (start_line 순)


def build_effective_tree(
    code_nodes: list[CodeNode],
) -> tuple[dict[str, str | None], dict[str, list[str]], set[str]]:
    """skip 노드를 투명 처리한 유효 부모/자식 맵.

    skip 노드(괄호)는 분석 제외 + 컨텍스트 투명 — 그 자식은 가장 가까운 non-skip 조상에
    붙는다 (부모가 자식 요약을 받게).

    Returns:
        ``(effective_parent, effective_children, analysis_ids)``
        - effective_parent[id]: non-skip 노드의 가장 가까운 non-skip 조상 (없으면 None).
        - effective_children[pid]: pid 를 유효부모로 갖는 non-skip 노드 list.
        - analysis_ids: 분석 대상(non-skip) 노드 id set.
    """
    parent_by_id = {f.id: f.parent_id for f in code_nodes}
    skip_ids = {f.id for f in code_nodes if f.own_ast_type() in _SKIP_ANALYSIS_TYPES}
    analysis_ids = [f.id for f in code_nodes if f.id not in skip_ids]

    effective_parent: dict[str, str | None] = {}
    for nid in analysis_ids:
        p = parent_by_id.get(nid)
        while p is not None and p in skip_ids:
            p = parent_by_id.get(p)
        effective_parent[nid] = p
    effective_children: dict[str, list[str]] = defaultdict(list)
    for nid in analysis_ids:
        p = effective_parent[nid]
        if p is not None:
            effective_children[p].append(nid)
    return effective_parent, dict(effective_children), set(analysis_ids)


def plan_batches(
    round_ids: list[str],
    effective_parent: dict[str, str | None],
    start_line_of: dict[str, int],
    token_of: dict[str, int],
    max_tokens: int,
) -> list[Batch]:
    """한 라운드의 노드들 → 같은 유효 부모 형제 토큰버짓 배치.

    - 유효 부모별로 묶고 start_line 순 정렬 후 토큰버짓까지 greedy 병합.
    - 루트(유효부모 None) = 서로 다른 모듈 → 각자 solo 배치 (병합 X).
    - 한 노드가 버짓 초과여도 단독 배치로 보장 (드롭 0).
    """
    by_parent: dict[str | None, list[str]] = defaultdict(list)
    for nid in round_ids:
        by_parent[effective_parent.get(nid)].append(nid)

    batches: list[Batch] = []
    for pid, sibs in by_parent.items():
        sibs_sorted = sorted(sibs, key=lambda i: start_line_of.get(i, 0))
        if pid is None:
            batches.extend(Batch(None, (nid,)) for nid in sibs_sorted)   # 루트 = solo reduce
            continue
        cur: list[str] = []
        cur_tok = 0
        for nid in sibs_sorted:
            t = token_of.get(nid, 0)
            if cur and cur_tok + t > max_tokens:
                batches.append(Batch(pid, tuple(cur)))
                cur, cur_tok = [], 0
            cur.append(nid)
            cur_tok += t
        if cur:
            batches.append(Batch(pid, tuple(cur)))
    return batches
