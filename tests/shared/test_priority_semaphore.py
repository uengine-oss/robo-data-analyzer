"""PrioritySemaphore 계약 — 총량 고정 + 우선순위 순서 + 취소 안전 (spec 096).

이 세마포어의 존재 이유는 "후처리를 겹치되 총 in-flight 를 늘리지 않는다"이다.
그래서 두 가지를 못 박는다: **동시 보유 수가 절대 value 를 넘지 않는다**,
그리고 **낮은 priority 가 먼저 깨어난다**.
"""
from __future__ import annotations

import asyncio
import unittest

from shared.concurrency.priority_semaphore import PrioritySemaphore


class PrioritySemaphoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_observability_counts_only_granted_slots(self) -> None:
        sem = PrioritySemaphore(1)
        await sem.acquire()
        waiter = asyncio.create_task(sem.acquire())
        await asyncio.sleep(0)
        self.assertEqual((1, 1, 1, 1), (
            sem.capacity, sem.in_use, sem.peak_in_use, sem.waiting,
        ))
        sem.release()
        await waiter
        self.assertEqual((1, 1, 0), (sem.in_use, sem.peak_in_use, sem.waiting))
        sem.release()
        self.assertEqual((0, 1, 1), (sem.in_use, sem.peak_in_use, sem.free))

    async def test_never_exceeds_total_slots(self) -> None:
        sem = PrioritySemaphore(3)
        live = 0
        peak = 0

        async def worker(priority: int) -> None:
            nonlocal live, peak
            async with sem.at(priority):
                live += 1
                peak = max(peak, live)
                await asyncio.sleep(0.01)
                live -= 1

        await asyncio.gather(*(worker(i % 2) for i in range(20)))
        self.assertEqual(peak, 3, "총 보유 슬롯이 value 를 넘었다 — 총량 계약 위반")
        self.assertEqual(live, 0)

    async def test_lower_priority_value_wakes_first(self) -> None:
        sem = PrioritySemaphore(1)
        order: list[str] = []

        async with sem.at(0):                     # 슬롯 1개를 잡아 전부 대기시킨다
            async def waiter(name: str, priority: int) -> None:
                async with sem.at(priority):
                    order.append(name)

            tasks = [
                asyncio.ensure_future(waiter("post-a", 1)),
                asyncio.ensure_future(waiter("post-b", 1)),
            ]
            await asyncio.sleep(0)                # 두 후처리 대기자를 먼저 줄 세운다
            tasks.append(asyncio.ensure_future(waiter("step7", 0)))
            await asyncio.sleep(0)

        await asyncio.gather(*tasks)
        self.assertEqual(
            order, ["step7", "post-a", "post-b"],
            "나중에 온 임계경로(step7)가 먼저 온 후처리 뒤로 밀렸다",
        )

    async def test_same_priority_is_fifo(self) -> None:
        sem = PrioritySemaphore(1)
        order: list[int] = []

        async with sem.at(0):
            async def waiter(n: int) -> None:
                async with sem.at(1):
                    order.append(n)

            tasks = []
            for n in range(5):
                tasks.append(asyncio.ensure_future(waiter(n)))
                await asyncio.sleep(0)

        await asyncio.gather(*tasks)
        self.assertEqual(order, [0, 1, 2, 3, 4])

    async def test_priority_cannot_bypass_one_waiter_beyond_capacity(self) -> None:
        sem = PrioritySemaphore(1)
        await sem.acquire()
        order: list[str] = []

        async def waiter(name: str, priority: int) -> None:
            await sem.acquire(priority)
            order.append(name)

        low = asyncio.create_task(waiter("low", 10))
        await asyncio.sleep(0)
        high1 = asyncio.create_task(waiter("high1", -10))
        await asyncio.sleep(0)
        sem.release()
        await high1

        high2 = asyncio.create_task(waiter("high2", -10))
        await asyncio.sleep(0)
        # low was bypassed once, equal to capacity=1. A later critical waiter
        # therefore cannot overtake it again.
        sem.release()
        await low
        sem.release()
        await high2
        sem.release()

        self.assertEqual(order, ["high1", "low", "high2"])

    async def test_cancelled_waiter_does_not_leak_a_slot(self) -> None:
        sem = PrioritySemaphore(1)

        async with sem.at(0):
            doomed = asyncio.ensure_future(_hold(sem, 1))
            await asyncio.sleep(0)
            doomed.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await doomed

        # 취소된 대기자가 슬롯을 물고 가지 않았다면 다시 잡을 수 있다.
        await asyncio.wait_for(_hold(sem, 0), timeout=1)

    async def test_weighted_lanes_are_fair_and_work_conserving(self) -> None:
        sem = PrioritySemaphore(1, fair_weights={"summary": 2, "rules": 1})
        await sem.acquire()
        order: list[str] = []

        async def waiter(name: str, lane: str) -> None:
            async with sem.at(0, lane):
                order.append(name)

        tasks = [
            asyncio.create_task(waiter("s1", "summary")),
            asyncio.create_task(waiter("s2", "summary")),
            asyncio.create_task(waiter("s3", "summary")),
            asyncio.create_task(waiter("s4", "summary")),
            asyncio.create_task(waiter("r1", "rules")),
            asyncio.create_task(waiter("r2", "rules")),
        ]
        await asyncio.sleep(0)
        sem.release()
        await asyncio.gather(*tasks)
        self.assertEqual(order[:3], ["s1", "r1", "s2"])
        self.assertEqual(set(order), {"s1", "s2", "s3", "s4", "r1", "r2"})

        rules_only = [
            asyncio.create_task(waiter("r3", "rules")),
            asyncio.create_task(waiter("r4", "rules")),
        ]
        await asyncio.gather(*rules_only)
        self.assertEqual(order[-2:], ["r3", "r4"])

    async def test_default_context_manager_is_drop_in(self) -> None:
        sem = PrioritySemaphore(2)
        async with sem:
            self.assertEqual(sem.free, 1)
        self.assertEqual(sem.free, 2)


async def _hold(sem: PrioritySemaphore, priority: int) -> None:
    async with sem.at(priority):
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
