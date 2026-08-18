"""PrioritySemaphore — 총량이 고정된 우선순위 슬롯 (spec 096).

``asyncio.Semaphore`` 는 FIFO 다. 한 run 안에서 **급한 일과 덜 급한 일이 같은 슬롯을
나눠 쓸 때** FIFO 는 나중에 도착한 급한 일을 먼저 도착한 덜 급한 일 뒤에 세운다.
그래서 후처리 수백 콜을 같은 슬롯에 넣으면 임계경로인 step7 노드가 그 뒤로 밀린다.

이 세마포어는 **총량은 그대로 두고 깨우는 순서만** 바꾼다:

- ``async with sem:``        → 우선순위 0 (``asyncio.Semaphore`` 드롭인)
- ``async with sem.at(1):``  → 명시 우선순위. 낮을수록 먼저.
- 같은 우선순위는 도착 순(FIFO).

**총 in-flight 를 늘리는 용도가 아니다.** 후처리에 세마포어를 하나 더 만들면 서버로 가는
동시 요청이 2배가 되고, 그건 이미 실측으로 닫힌 길이다(콜 단위 상한 +28%). 슬롯 수는
하나로 유지한 채 **비는 슬롯의 임자만** 정하는 것이 이 클래스의 전부다.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(order=True)
class _Waiter:
    priority: int
    seq: int
    future: asyncio.Future = field(compare=False)
    lane: str = field(default="default", compare=False)
    bypasses: int = field(default=0, compare=False)


class PrioritySemaphore:
    """Fixed-capacity priority queue with optional work-conserving fair lanes.

    Without ``fair_weights``, fresh waiters keep ``priority, FIFO`` order and a waiter
    owns the next slot after ``capacity`` bypasses. With weights, smooth weighted
    round-robin chooses an active lane and priority/FIFO chooses inside it. Weights are
    contention shares, never reservations, so idle lanes lend all capacity.
    """

    def __init__(
        self,
        value: int,
        *,
        fair_weights: Optional[Mapping[str, int]] = None,
        lane_caps: Optional[Mapping[str, int]] = None,
    ) -> None:
        if value < 1:
            raise ValueError(f"PrioritySemaphore value must be >= 1; got {value}")
        self._capacity = value
        self._value = value
        self._in_use = 0
        self._peak_in_use = 0
        self._waiters: list[_Waiter] = []
        self._seq = itertools.count()
        self._fair_weights = dict(fair_weights or {})
        if any(not lane or weight < 1 for lane, weight in self._fair_weights.items()):
            raise ValueError("fair_weights require non-empty lanes and weights >= 1")
        self._lane_caps = dict(lane_caps or {})
        if any(
            not lane or cap < 1 or cap > value
            for lane, cap in self._lane_caps.items()
        ):
            raise ValueError(
                "lane_caps require non-empty lanes and values between 1 and capacity"
            )
        self._lane_in_use: dict[str, int] = {}
        self._lane_current: dict[str, int] = {
            lane: 0 for lane in self._fair_weights
        }

    @property
    def free(self) -> int:
        """지금 남은 슬롯 수 — 관측용(대기자 수는 포함하지 않는다)."""
        return self._value

    @property
    def in_use(self) -> int:
        """현재 실제로 발급된 슬롯 수(대기자는 제외)."""
        return self._in_use

    def in_use_for(self, lane: str) -> int:
        """Return granted slots for one lane (queued waiters are excluded)."""
        return self._lane_in_use.get(lane or "default", 0)

    @property
    def peak_in_use(self) -> int:
        """생성 이후 실제 동시 점유의 최댓값."""
        return self._peak_in_use

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def waiting(self) -> int:
        return sum(1 for waiter in self._waiters if not waiter.future.cancelled())

    async def acquire(self, priority: int = 0, lane: Optional[str] = None) -> None:
        selected_lane = lane or "default"
        if (
            self._value > 0
            and not any(
                not waiter.future.cancelled()
                and self._lane_has_capacity(waiter.lane)
                for waiter in self._waiters
            )
            and self._lane_has_capacity(selected_lane)
        ):
            self._value -= 1
            self._grant(selected_lane)
            return
        # 슬롯이 있어도 **대기자가 있으면 줄을 선다** — 뒤늦게 온 낮은 우선순위가
        # 새치기해 높은 우선순위 대기자를 굶기지 않게(그리고 순서 계약을 지키게).
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        heapq.heappush(
            self._waiters,
            _Waiter(
                priority=priority,
                seq=next(self._seq),
                future=future,
                lane=selected_lane,
            ),
        )
        try:
            await future
        except asyncio.CancelledError:
            # 이미 슬롯을 받은 뒤 취소됐다면 반납해야 한다 — 안 그러면 슬롯이 샌다.
            if future.done() and not future.cancelled():
                self.release(selected_lane)
            else:
                self._drop(future)
            raise

    def release(self, lane: Optional[str] = None) -> None:
        """슬롯 1개 반납 — 대기자가 있으면 그중 최우선에게 바로 넘긴다."""
        selected_lane = lane or "default"
        if self._in_use < 1 or self._lane_in_use.get(selected_lane, 0) < 1:
            raise RuntimeError("PrioritySemaphore released without an acquired slot")
        self._in_use -= 1
        self._lane_in_use[selected_lane] -= 1
        waiter = self._pop_next_waiter()
        if waiter is None:
            self._value += 1
            return
        self._grant(waiter.lane)
        waiter.future.set_result(True)

    def _grant(self, lane: str) -> None:
        self._in_use += 1
        self._lane_in_use[lane] = self._lane_in_use.get(lane, 0) + 1
        self._peak_in_use = max(self._peak_in_use, self._in_use)

    def _lane_has_capacity(self, lane: str) -> bool:
        cap = self._lane_caps.get(lane)
        return cap is None or self._lane_in_use.get(lane, 0) < cap

    def _drop(self, future: asyncio.Future) -> None:
        """취소된 대기자를 힙에서 제거 — 힙이 죽은 항목으로 자라지 않게."""
        self._waiters = [w for w in self._waiters if w.future is not future]
        heapq.heapify(self._waiters)

    def _pop_next_waiter(self) -> Optional[_Waiter]:
        """Choose strict priority unless a waiter reached the overtaking bound."""
        self._waiters = [w for w in self._waiters if not w.future.cancelled()]
        eligible = [
            waiter for waiter in self._waiters
            if self._lane_has_capacity(waiter.lane)
        ]
        if not eligible:
            return None
        heapq.heapify(self._waiters)

        if self._fair_weights:
            return self._pop_weighted_waiter({waiter.lane for waiter in eligible})

        bounded = [w for w in eligible if w.bypasses >= self._capacity]
        if bounded:
            selected = min(bounded, key=lambda w: (-w.bypasses, w.seq))
            self._waiters.remove(selected)
            heapq.heapify(self._waiters)
        else:
            selected = min(eligible)
            self._waiters.remove(selected)
            heapq.heapify(self._waiters)

        for waiting in self._waiters:
            waiting.bypasses += 1
        return selected

    def _pop_weighted_waiter(self, active_lanes: set[str]) -> _Waiter:
        """Smooth weighted round-robin across active lanes, priority/FIFO within one.

        Inactive lanes receive no credit, so a lane cannot accumulate an unbounded
        burst while idle. Unknown lanes use weight 1. If only one lane is active it
        borrows every slot; capacity is never reserved or left idle.
        """
        for lane in list(self._lane_current):
            if lane not in active_lanes:
                self._lane_current[lane] = 0

        total_weight = 0
        for lane in active_lanes:
            weight = self._fair_weights.get(lane, 1)
            total_weight += weight
            self._lane_current[lane] = self._lane_current.get(lane, 0) + weight

        configured_order = {lane: index for index, lane in enumerate(self._fair_weights)}
        selected_lane = min(
            active_lanes,
            key=lambda lane: (
                -self._lane_current[lane],
                configured_order.get(lane, len(configured_order)),
                lane,
            ),
        )
        self._lane_current[selected_lane] -= total_weight
        selected = min(
            (waiter for waiter in self._waiters if waiter.lane == selected_lane),
            key=lambda waiter: (waiter.priority, waiter.seq),
        )
        self._waiters.remove(selected)
        heapq.heapify(self._waiters)
        return selected

    def at(self, priority: int, lane: Optional[str] = None) -> "_PrioritySlot":
        """``async with sem.at(1):`` — 명시 우선순위로 슬롯 1개."""
        return _PrioritySlot(self, priority, lane)

    async def __aenter__(self) -> "PrioritySemaphore":
        await self.acquire(0)
        return self

    async def __aexit__(self, *exc_info) -> None:
        self.release("default")


class _PrioritySlot:
    """``PrioritySemaphore.at(p)`` 가 돌려주는 1회용 컨텍스트 매니저."""

    __slots__ = ("_sem", "_priority", "_lane")

    def __init__(
        self,
        sem: PrioritySemaphore,
        priority: int,
        lane: Optional[str],
    ) -> None:
        self._sem = sem
        self._priority = priority
        self._lane = lane

    async def __aenter__(self) -> PrioritySemaphore:
        await self._sem.acquire(self._priority, self._lane)
        return self._sem

    async def __aexit__(self, *exc_info) -> None:
        self._sem.release(self._lane)
