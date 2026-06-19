"""FrameworkContextBuilder — Framework 의 분석 컨텍스트 빌더 (주입 구현체).

``build_context_step`` 이 ``strategy.context_builder`` 로 주입받는다.

Framework 는 strategy 별 추가 컨텍스트가 없다 — callee 의존성은 base ``ctx.callees``
(호출 그래프) 로 이미 표현되고, callee 의 summary / one_liner 는 step6 Bottom-up 진행 중
``contexts`` dict 라이브 조회(``three_split_llm_fanout._format_callee_summaries``)로
가져온다. 따라서 ``augment`` 는 항등(ctx 무변경) — 모든 strategy 가 context_builder 를
제공한다는 DI 계약을 지키기 위한 구현체.
"""
from __future__ import annotations

from data_models.analysis_context import AnalysisContext
from data_models.graph_nodes import CodeNode


class FrameworkContextBuilder:
    """Framework: strategy 별 추가 컨텍스트 없음 — ``augment`` 는 항등."""

    async def augment(
        self,
        ctx: AnalysisContext,
        code_node: CodeNode,
        state: dict,
    ) -> AnalysisContext:
        """Framework 는 추가 컨텍스트가 없다 — ``ctx`` 를 그대로 반환."""
        return ctx
