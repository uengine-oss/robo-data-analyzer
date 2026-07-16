"""토큰 비용 회로 차단기 — $400 사고 재발 방지.

run 시작 시 ``CostBudget`` 1개 생성. 모든 LLM 호출의 ``CostTrackingMiddleware`` 가
``add()`` 호출 → 비용 누적. ``ROBO_BUDGET_RUN_USD`` 임계 초과 시
``BudgetExceededError`` 발생 → 파이프라인 자동 중단.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from system.env_catalog import ENV


class BudgetExceededError(RuntimeError):
    """run 당 USD 임계 초과 — 파이프라인 자동 중단."""

    def __init__(self, spent_usd: float, limit_usd: float):
        super().__init__(
            f"Budget exceeded: spent ${spent_usd:.4f} > limit ${limit_usd:.2f} "
            f"(ROBO_BUDGET_RUN_USD)"
        )
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd


@dataclass
class CostBudget:
    """run 1회의 누적 비용 추적.

    ``spent_usd`` 가 ``limit_usd`` 초과 시 ``add()`` / ``add_sync()`` 가
    ``BudgetExceededError`` raise.
    """
    limit_usd: float = field(default_factory=lambda: ENV.budget.run_usd)
    spent_usd: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def add(self, cost_usd: float) -> None:
        """비용 누적 (비동기) — 임계 초과 시 ``BudgetExceededError`` raise."""
        async with self._lock:
            self.spent_usd += cost_usd
            if self.spent_usd > self.limit_usd:
                raise BudgetExceededError(self.spent_usd, self.limit_usd)

    def add_sync(self, cost_usd: float) -> None:
        """비용 누적 (동기) — 미들웨어가 sync 환경에서 호출."""
        self.spent_usd += cost_usd
        if self.spent_usd > self.limit_usd:
            raise BudgetExceededError(self.spent_usd, self.limit_usd)
