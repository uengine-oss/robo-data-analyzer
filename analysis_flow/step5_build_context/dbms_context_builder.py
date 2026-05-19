"""DbmsContextBuilder — DBMS 의 분석 컨텍스트 빌더.

DBMS 의 nested range 는 parent / 직전 sibling 컨텍스트가 의미 분석에 핵심.

``augment()`` 가 ``AnalysisContext.extra`` 에 다음 채움 (LLM 호출 0회, 결정적):
- ``parent_id``: 부모 Function 식별자 (PARENT_OF 엣지)
- ``parent_summary``: 부모의 summary 슬롯 (step6 가 Bottom-up 으로 채움)
- ``sibling_ids``: 같은 부모 아래 형제 식별자 (sorted tuple)
- ``sibling_summaries``: 형제 summary 슬롯 (step6 가 채움)

**Framework vs DBMS 차이**: 같은 ``AnalysisContext`` dataclass 가 양쪽을 흡수.
Framework 는 ``ctx.callees`` (호출 그래프) 사용, DBMS 는 ``ctx.extra["parent_*"]`` 사용.

**Plan §6.3 vs 구현 차이 명시**:
Plan §6.3 에는 ``prompts/dbms/node_context.yaml`` 가 명시되어 있으나, refact 설계에서
DBMS 의 컨텍스트 빌드는 LLM 0 회 결정론으로 흡수 (parent/sibling 식별은 PARENT_OF
엣지 + state 만으로 결정 가능). 별도 yaml 불필요 → 추가하지 않음.
LLM 호출은 ``step6_analyze_function`` 의 3 분할에서만 발생.
"""
from __future__ import annotations

from data_models.analysis_context import AnalysisContext
from data_models.graph_nodes import Function


class DbmsContextBuilder:
    """DBMS: parent + sibling 컨텍스트 부착."""

    async def augment(
        self,
        ctx: AnalysisContext,
        function: Function,
        state: dict,
    ) -> AnalysisContext:
        """``ctx.extra`` 에 parent / sibling 슬롯 + 식별자 리스트."""
        ctx.extra["parent_id"] = function.parent_id
        ctx.extra["parent_summary"] = None     # step6 가 Bottom-up 으로 채움
        ctx.extra["sibling_summaries"] = []    # 직전 sibling 들의 summary

        # 같은 parent 아래 sibling 식별자 수집 (summary 는 차후 채움)
        if function.parent_id:
            parent_of_edges = state.get("parent_of_edges", [])
            sibling_ids = [
                e.child_function_id for e in parent_of_edges
                if e.parent_function_id == function.parent_id
                and e.child_function_id != function.function_id
            ]
            ctx.extra["sibling_ids"] = tuple(sorted(sibling_ids))
        else:
            ctx.extra["sibling_ids"] = ()
        return ctx
