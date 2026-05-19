"""FrameworkContextBuilder — Framework 의 분석 컨텍스트 빌더.

``build_context_step`` 이 ``strategy.context_builder`` 로 주입받음.

``augment()`` 가 ``AnalysisContext.extra`` 에 다음 채움:
- ``callee_summaries``: callee function 의 summary dict (Bottom-up DAG 의존성).
  Bottom-up Round 진행 시 채워짐 — 처음에는 빈 dict.
- ``callee_one_liners``: 큰 callee 의 사전 압축 one_liner.

caller 분석 시 callee 본문 통째 노출 X — summary 또는 one_liner 만.
"""
from __future__ import annotations

from data_models.analysis_context import AnalysisContext
from data_models.graph_nodes import Function


class FrameworkContextBuilder:
    """Framework: callee summary 컨텍스트 부착."""

    async def augment(
        self,
        ctx: AnalysisContext,
        function: Function,
        state: dict,
    ) -> AnalysisContext:
        """``ctx.extra`` 에 callee 관련 컨텍스트 슬롯 마련 (실제 채움은 step6).

        ``build_context_step`` 시점에는 callee summary 아직 없음 (Bottom-up DAG 가
        leaf → root 진행하며 채움). 여기서는 슬롯 + callee 식별자 리스트만.
        """
        ctx.extra["callee_summaries"] = {}     # callee_id → summary (step6 가 채움)
        ctx.extra["callee_one_liners"] = {}    # callee_id → one_liner (큰 함수 사전 압축)
        return ctx
