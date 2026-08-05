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
from typing import Optional


class PrioritySemaphore:
    """총량 ``value`` 고정. 대기자는 ``(priority, 도착순)`` 오름차순으로 깨운다."""

    def __init__(self, value: int) -> None:
        if value < 1:
            raise ValueError(f"PrioritySemaphore value must be >= 1; got {value}")
        self._capacity = value
        self._value = value
        self._in_use = 0
        self._peak_in_use = 0
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._seq = itertools.count()

    @property
    def free(self) -> int:
        """지금 남은 슬롯 수 — 관측용(대기자 수는 포함하지 않는다)."""
        return self._value

    @property
    def in_use(self) -> int:
        """현재 실제로 발급된 슬롯 수(대기자는 제외)."""
        return self._in_use

    @property
    def peak_in_use(self) -> int:
        """생성 이후 실제 동시 점유의 최댓값."""
        return self._peak_in_use

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def waiting(self) -> int:
        return sum(1 for _, _, future in self._waiters if not future.cancelled())

    async def acquire(self, priority: int = 0) -> None:
        if self._value > 0 and not self._waiters:
            self._value -= 1
            self._in_use += 1
            self._peak_in_use = max(self._peak_in_use, self._in_use)
            return
        # 슬롯이 있어도 **대기자가 있으면 줄을 선다** — 뒤늦게 온 낮은 우선순위가
        # 새치기해 높은 우선순위 대기자를 굶기지 않게(그리고 순서 계약을 지키게).
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, next(self._seq), future))
        try:
            await future
        except asyncio.CancelledError:
            # 이미 슬롯을 받은 뒤 취소됐다면 반납해야 한다 — 안 그러면 슬롯이 샌다.
            if future.done() and not future.cancelled():
                self.release()
            else:
                self._drop(future)
            raise

    def release(self) -> None:
        """슬롯 1개 반납 — 대기자가 있으면 그중 최우선에게 바로 넘긴다."""
        while self._waiters:
            _priority, _seq, future = heapq.heappop(self._waiters)
            if future.cancelled():
                continue          # 취소된 대기자는 건너뛴다 (슬롯은 다음 사람에게)
            future.set_result(True)
            return
        if self._in_use < 1:
            raise RuntimeError("PrioritySemaphore released without an acquired slot")
        self._in_use -= 1
        self._value += 1

    def _drop(self, future: asyncio.Future) -> None:
        """취소된 대기자를 힙에서 제거 — 힙이 죽은 항목으로 자라지 않게."""
        self._waiters = [w for w in self._waiters if w[2] is not future]
        heapq.heapify(self._waiters)

    def at(self, priority: int) -> "_PrioritySlot":
        """``async with sem.at(1):`` — 명시 우선순위로 슬롯 1개."""
        return _PrioritySlot(self, priority)

    async def __aenter__(self) -> "PrioritySemaphore":
        await self.acquire(0)
        return self

    async def __aexit__(self, *exc_info) -> None:
        self.release()


class _PrioritySlot:
    """``PrioritySemaphore.at(p)`` 가 돌려주는 1회용 컨텍스트 매니저."""

    __slots__ = ("_sem", "_priority")

    def __init__(self, sem: PrioritySemaphore, priority: int) -> None:
        self._sem = sem
        self._priority = priority

    async def __aenter__(self) -> PrioritySemaphore:
        await self._sem.acquire(self._priority)
        return self._sem

    async def __aexit__(self, *exc_info) -> None:
        self._sem.release()
