"""AnalysisHandoff — step7이 확정한 결과를 후처리가 배리어 없이 받는 run 스코프 채널.

**왜 필요한가.** LangGraph 는 노드 간 배리어가 기본이라 "step7 노드 → 후처리 노드" 순서면
후처리는 step7 의 **마지막 콜**까지 기다린다. 그런데 후처리가 실제로 읽는 값은 step7 의
**일부 분할**에서만 나온다 — 모듈요약·컴포넌트명·용어사전은 #1 summary 하나만 읽는다.
Summary는 Rule보다 짧아, 전 노드의 Summary가 끝나는 시각은 노드 전체가 끝나는 시각보다
한참 이르다(기준선 실측 +592 vs +692초).

그 차이만큼 GPU 슬롯이 빈다. 이 채널은 **의존이 실제로 풀린 시점**을 후처리에 알려
그 빈 슬롯을 채우게 한다. step7 **안에서** 한 일(CALLS 의존을 callee summary
확정 시점에 조기 해제)을 step7 **바깥**에 대해 한 번 더 하는 것이다.

**무엇을 바꾸지 않는가.** 시작 시점만 바꾼다. 각 후처리는 자신이 소비하는 전 대상
노드의 결과가 확정된 뒤에 시작하므로 프롬프트 입력 집합이 종전과 같다 — 부분 집합으로
앞당겨 시작하면 용어 우주·별칭 후보·모듈 멤버 목록이 달라져 산출물이 바뀐다. 그건 이
채널의 계약이 아니다.

**교착 금지.** 생산자는 ``finally`` 에서 반드시 ``close()`` 또는 ``abort()`` 한다.
게이트가 안 열리면 LangGraph superstep 이 끝나지 않는다 — 조용한 교착 대신 즉시 표면화.
"""
from __future__ import annotations

import asyncio
from typing import Iterable, Optional

from graph.schema.graph_nodes import CodeNode
from shared.concurrency.priority_semaphore import PrioritySemaphore

# 슬롯 우선순위 — 낮을수록 먼저. step7 이 임계경로라 항상 앞선다.
STEP7_PRIORITY = 0
POSTPROCESS_PRIORITY = 1
SUMMARY_WAKE_PRIORITY = -10_000_000
SUMMARY_LANE = "summary"
RULES_LANE = "rules"
POSTPROCESS_LANE = "postprocess"

# Smooth weighted fairness protects dependency-unlocking summaries without starving
# evidence-heavy Rule work or the post-processing that overlaps Step 7. These are
# shares under contention, not reservations: an idle lane lends all capacity.
ANALYSIS_LLM_LANE_WEIGHTS = {
    SUMMARY_LANE: 4,
    RULES_LANE: 1,
    POSTPROCESS_LANE: 1,
}

def analysis_llm_slots() -> PrioritySemaphore:
    from shared.config.app_settings import SETTINGS
    from llm.configs.config_loader import load_llm_config

    configured = load_llm_config(SETTINGS.analysis_llm.config)
    limits = [
        SETTINGS.analysis_pipeline.max_concurrency,
        SETTINGS.analysis_pipeline.llm_max_concurrency,
    ]
    if configured.max_concurrency is not None:
        limits.append(configured.max_concurrency)
    llm_capacity = min(limits)
    return PrioritySemaphore(
        llm_capacity,
        fair_weights=ANALYSIS_LLM_LANE_WEIGHTS,
    )


class AnalysisHandoffAborted(RuntimeError):
    """분석이 끝나기 전에 채널이 닫혔다 — 대기 중인 후처리는 진행할 수 없다."""


def handoff_of(cfg: dict) -> "AnalysisHandoff":
    """Return the one run-scoped handoff; a missing channel is a contract error."""
    existing = cfg.get("analysis_handoff")
    if not isinstance(existing, AnalysisHandoff):
        raise ValueError("analysis_handoff is required for the analysis pipeline")
    return existing


class AnalysisHandoff:
    """step7 → 후처리 인계 채널 + run 전체가 공유하는 단 하나의 LLM 슬롯.

    슬롯을 이 객체가 갖는 이유: "겹친다"와 "총량을 안 늘린다"는 한 결정의 양면이라
    따로 두면 둘 중 하나만 지켜지는 상태가 만들어진다. 겹치기를 도입하는 곳이
    슬롯의 단일 진실도 함께 갖는다.
    """

    def __init__(self, slots: PrioritySemaphore) -> None:
        self.slots = slots
        self._expected: set[str] = set()
        self._pending_summary: set[str] = set()
        self._pending_node: set[str] = set()
        self._published: dict[str, CodeNode] = {}
        self._summaries_ready = asyncio.Event()
        self._analysis_ready = asyncio.Event()
        self._error: Optional[BaseException] = None
        self._expect_called = False

    # ── 생산자(step7) ────────────────────────────────────────────

    def expect(self, code_ids: Iterable[str]) -> None:
        """분석 대상 id 등록. 대상이 0개면 게이트를 즉시 연다."""
        self._expect_called = True
        self._expected = set(code_ids)
        self._pending_summary = set(self._expected)
        self._pending_node = set(self._expected)
        self._refresh()

    def publish_summary(self, node: CodeNode) -> None:
        """한 노드의 #1 summary 확정 직후 호출 — 그 ``CodeNode`` 객체를 인계 목록에 싣는다.

        객체를 그대로 싣는다(복사 X). step7 이 이어서 #2·#3 결과를 같은 객체에 채우므로,
        ``analysis()`` 를 기다린 소비자는 자동으로 완전한 노드를 본다.
        """
        self._published[node.id] = node
        self._pending_summary.discard(node.id)
        self._refresh()

    def resolve(self, code_id: str) -> None:
        """노드 처리 완료(성공·실패·스킵 무관). 미공개 노드를 대기에서 뺀다."""
        self._pending_summary.discard(code_id)
        self._pending_node.discard(code_id)
        self._refresh()

    def close(self) -> None:
        """생산이 끝났다 — 남은 대기자를 현재 값으로 진행시킨다.

        ``expect`` 를 부르기 전에 step7 이 빠져나간 경우(분석 대상 0)도 여기서 열린다.
        안 그러면 후처리가 영원히 기다려 superstep 이 끝나지 않는다.
        """
        self._expect_called = True
        self._pending_summary.clear()
        self._pending_node.clear()
        self._refresh()

    def abort(self, error: BaseException) -> None:
        """생산이 실패했다 — 대기자에게 원인을 전달하고 모든 게이트를 연다."""
        if self._error is None:
            self._error = error
        self.close()

    # ── 소비자(후처리) ──────────────────────────────────────────

    async def summaries(self) -> list[CodeNode]:
        """전 대상의 #1 이 확정될 때까지 대기 → 공개된 코드노드 목록."""
        await self._wait(self._summaries_ready)
        return list(self._published.values())

    async def analysis(self) -> list[CodeNode]:
        """step7 노드 전체 완료까지 대기 → 공개된 코드노드 목록."""
        await self._wait(self._analysis_ready)
        return list(self._published.values())

    def merge(self, code_nodes: list[CodeNode]) -> list[CodeNode]:
        """state 의 코드노드 목록에서 분석된 노드를 **공개된 객체로 치환**한 목록.

        LangGraph 가 병렬 그룹 멤버에게 state 를 어떻게 전달하든(같은 객체든 사본이든)
        후처리가 확정된 분석 결과를 본다는 계약을 이 한 곳이 보장한다.
        """
        published = self._published
        return [published.get(fn.id, fn) for fn in code_nodes]

    # ── 내부 ────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if not self._expect_called:
            return
        if not self._pending_summary:
            self._summaries_ready.set()
        if not self._pending_node:
            self._analysis_ready.set()

    async def _wait(self, event: asyncio.Event) -> None:
        await event.wait()
        if self._error is not None:
            raise AnalysisHandoffAborted(
                "분석이 끝나기 전에 인계 채널이 닫혔다 — 후처리를 진행할 수 없다"
            ) from self._error
