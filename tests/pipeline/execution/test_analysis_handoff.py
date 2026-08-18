"""AnalysisHandoff 계약 — 게이트 시점·교착 금지·인계 목록 (spec 096).

가장 비싼 실패는 "게이트가 안 열려 run 이 안 끝나는 것"이라 그 경로를 우선 못 박는다.
그 다음이 "전 대상이 확정되기 전에 열려 후처리가 부분 집합으로 시작하는 것" — 이건
산출물이 조용히 달라지는 경로다.
"""
from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field

from pipeline.execution.analysis_handoff import (
    AnalysisHandoff,
    AnalysisHandoffAborted,
    RULES_LANE,
    analysis_llm_slots,
    handoff_of,
)
from shared.config.settings import SETTINGS
from shared.concurrency.priority_semaphore import PrioritySemaphore


@dataclass
class _Node:
    id: str
    summary: str | None = None
    extra: dict = field(default_factory=dict)


def _handoff() -> AnalysisHandoff:
    return AnalysisHandoff(PrioritySemaphore(4))


class SummaryGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_opens_only_after_every_target_is_accounted_for(self) -> None:
        h = _handoff()
        h.expect(["a", "b", "c"])
        waiter = asyncio.ensure_future(h.summaries())
        await asyncio.sleep(0)

        h.publish_summary(_Node("a", "sa"))
        h.publish_summary(_Node("b", "sb"))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done(), "일부만 확정됐는데 게이트가 열렸다 — 부분 입력으로 시작한다")

        h.publish_summary(_Node("c", "sc"))
        published = await asyncio.wait_for(waiter, timeout=1)
        self.assertEqual({n.id for n in published}, {"a", "b", "c"})

    async def test_failed_node_releases_the_gate_without_being_published(self) -> None:
        h = _handoff()
        h.expect(["ok", "boom"])
        h.publish_summary(_Node("ok", "s"))
        h.resolve("boom")            # 분석 실패·스킵 — 영원히 공개되지 않는다
        published = await asyncio.wait_for(h.summaries(), timeout=1)
        self.assertEqual([n.id for n in published], ["ok"])

    async def test_empty_target_set_opens_immediately(self) -> None:
        h = _handoff()
        h.expect([])
        self.assertEqual(await asyncio.wait_for(h.summaries(), timeout=1), [])
        self.assertEqual(await asyncio.wait_for(h.analysis(), timeout=1), [])

    async def test_close_before_expect_still_opens(self) -> None:
        """step7 이 ``expect`` 전에 빠져나가도(분석 대상 0) 후처리가 굶으면 안 된다."""
        h = _handoff()
        h.close()
        self.assertEqual(await asyncio.wait_for(h.summaries(), timeout=1), [])
        self.assertEqual(await asyncio.wait_for(h.analysis(), timeout=1), [])


class AnalysisGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_waits_for_node_completion_not_summary(self) -> None:
        h = _handoff()
        h.expect(["a"])
        waiter = asyncio.ensure_future(h.analysis())
        h.publish_summary(_Node("a", "s"))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done(), "#1 확정만으로 완료 게이트가 열렸다")

        h.resolve("a")
        self.assertEqual([n.id for n in await asyncio.wait_for(waiter, timeout=1)], ["a"])


class AbortTest(unittest.IsolatedAsyncioTestCase):
    async def test_abort_wakes_every_waiter_with_the_cause(self) -> None:
        h = _handoff()
        h.expect(["a"])
        waiters = [
            asyncio.ensure_future(h.summaries()),
            asyncio.ensure_future(h.analysis()),
            asyncio.ensure_future(h.modules()),
        ]
        await asyncio.sleep(0)
        h.abort(RuntimeError("step7 폭발"))
        for w in waiters:
            with self.assertRaises(AnalysisHandoffAborted):
                await asyncio.wait_for(w, timeout=1)


class MergeTest(unittest.TestCase):
    def test_merge_replaces_analyzed_nodes_and_keeps_the_rest(self) -> None:
        h = _handoff()
        h.expect(["a"])
        analyzed = _Node("a", "확정된 요약")
        h.publish_summary(analyzed)

        stale = _Node("a")                       # state 쪽 사본(요약 없음)
        structural = _Node("ctrl")               # 분석 대상 아님 — 그대로 유지
        merged = h.merge([stale, structural])

        self.assertIs(merged[0], analyzed, "분석된 노드가 확정본으로 치환되지 않았다")
        self.assertIs(merged[1], structural)


class StandaloneTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_config_yields_an_already_open_channel(self) -> None:
        """겹칠 상대가 없는 실행(단독 호출·테스트)에서 게이트가 교착이 되면 안 된다."""
        h = handoff_of({})
        self.assertEqual(await asyncio.wait_for(h.summaries(), timeout=1), [])
        self.assertEqual(await asyncio.wait_for(h.analysis(), timeout=1), [])
        self.assertEqual(await asyncio.wait_for(h.modules(), timeout=1), [])

    async def test_configured_channel_is_returned_as_is(self) -> None:
        h = _handoff()
        self.assertIs(handoff_of({"analysis_handoff": h}), h)


class SharedLlmAdmissionTest(unittest.TestCase):
    def test_llm_admission_is_bounded_independently_from_node_preparation(self) -> None:
        slots = analysis_llm_slots()
        self.assertEqual(
            slots.capacity,
            min(
                SETTINGS.analysis_pipeline.max_concurrency,
                SETTINGS.analysis_pipeline.llm_max_concurrency,
            ),
        )
        self.assertLessEqual(
            SETTINGS.analysis_pipeline.rule_max_concurrency,
            slots.capacity,
        )
        self.assertEqual(slots.in_use_for(RULES_LANE), 0)


if __name__ == "__main__":
    unittest.main()
