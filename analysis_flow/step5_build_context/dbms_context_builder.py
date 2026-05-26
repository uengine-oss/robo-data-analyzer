"""DbmsContextBuilder — DBMS 의 분석 컨텍스트 빌더 (주입 구현체).

DBMS Function 은 PROCEDURE 안에 IF/LOOP/DML 이 중첩된 트리(``parent_id``).
``augment()`` 가 ``AnalysisContext.extra`` 에 트리 메타를 채운다 (LLM 0회, 결정적):

- ``parent_id`` : 부모 Function 식별자 (PARENT_OF) — 조상 골격 context 추적용.
- ``child_ids`` : 직속 자식 Function 식별자들 — compact_body·자식 규칙 수집용.

**자식→부모 단방향 흐름**(사용자 결정): 자식이 먼저 분석되고(Bottom-up Round),
부모가 자식 summary·rules 를 받아 합성한다. 직전 형제 context 는 폐기 — 부모가 모든
자식을 통합하므로 lateral 형제 전달은 중복.

실제 컨텍스트 텍스트(compact 본문·조상 골격)는 step6 ``three_split_llm_fanout`` +
``range_body`` 가 ``contexts`` 라이브 조회로 구성. 여기서는 트리 식별자만 stash.
"""
from __future__ import annotations

from data_models.analysis_context import AnalysisContext
from data_models.graph_nodes import Function


class DbmsContextBuilder:
    """DBMS: nested-range 트리 메타(parent_id / child_ids) 부착."""

    def __init__(self) -> None:
        # parent_of_edges 리스트 1개당 children 맵 1회 빌드 후 재사용
        # (augment 는 함수마다 호출 — 매번 전체 스캔 방지).
        self._children: dict[str, tuple[str, ...]] = {}
        self._cache_token: int | None = None

    async def augment(
        self,
        ctx: AnalysisContext,
        function: Function,
        state: dict,
    ) -> AnalysisContext:
        """``ctx.extra`` 에 parent_id + child_ids 설정 (PARENT_OF 엣지 기반)."""
        edges = state.get("parent_of_edges", [])
        if self._cache_token != id(edges):
            grouped: dict[str, list[str]] = {}
            for e in edges:
                grouped.setdefault(e.parent_function_id, []).append(e.child_function_id)
            self._children = {p: tuple(sorted(c)) for p, c in grouped.items()}
            self._cache_token = id(edges)

        ctx.extra["parent_id"] = function.parent_id
        ctx.extra["child_ids"] = self._children.get(function.function_id, ())
        return ctx
