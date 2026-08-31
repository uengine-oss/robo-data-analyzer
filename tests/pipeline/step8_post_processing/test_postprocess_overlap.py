"""후처리 겹치기 계약 — 실제 LangGraph 그룹에서 검증 (spec 096).

이 슬라이스의 목적은 하나다: **후처리가 step7 의 마지막 콜을 기다리지 않는 것.**
그래서 여기서는 "step7 이 아직 안 끝났는데 후처리가 시작했는가"와
"후처리가 본 코드노드가 확정본인가"를 실제 그래프 실행으로 못 박는다.

step7 자리에 넣는 대역은 실제 step7 의 신호 순서를 그대로 흉내낸다 —
전 노드 #1 확정(``publish_summary``) → 한참 뒤 노드 완료(``resolve``).
기준선 콜드런에서 이 간격이 100초였고 그게 이 슬라이스가 채우려는 빈 구간이다.
"""
from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from typing import Callable, Optional
from unittest import mock

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from pipeline.execution.analysis_handoff import AnalysisHandoff
from pipeline.execution.langgraph_builder import build_langgraph_workflow
from pipeline.steps.step8_post_processing.build_module_summary_rollups_step import (
    _group_functions_by_module,
    build_module_summary_rollups_step,
)
from shared.concurrency.priority_semaphore import PrioritySemaphore


@dataclass
class _Node:
    id: str
    label: str = "FUNCTION"
    owner_id: str = "mod:m.c"
    file_path: str = "m.c"
    start_line: int = 1
    end_line: int = 9
    signature: str = "int f(void)"
    summary: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Module:
    id: str = "mod:m.c"
    name: str = "m.c"
    file_path: str = "m.c"
    start_line: int = 0
    end_line: int = 0
    label: str = "FILE"
    summary_rollup_function_ids: tuple[str, ...] = ()
    summary_rollup_summaries: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Strategy:
    steps: tuple
    name: str = "framework"
    prompts_dir: str = "pipeline/semantic_tasks/framework"
    analysis_targets: Callable = lambda nodes: {
        node.id for node in nodes if node.label == "FUNCTION"
    }


class PostprocessOverlapTest(unittest.IsolatedAsyncioTestCase):
    def test_module_rollup_uses_owner_id_and_function_id_order(self) -> None:
        later_but_first_id = _Node("code:a", start_line=20, end_line=29)
        earlier_but_last_id = _Node("code:z", start_line=2, end_line=9)

        grouped = _group_functions_by_module(
            [earlier_but_last_id, later_but_first_id],
            [_Module()],
        )

        self.assertEqual(
            [node.id for node in grouped["mod:m.c"]],
            ["code:a", "code:z"],
        )

    def test_module_rollup_excludes_non_function_nodes(self) -> None:
        routine = _Node("code:f", label="FUNCTION")
        control = _Node("code:f:IF@2", label="IF")

        self.assertEqual(
            _group_functions_by_module([control, routine], [_Module()]),
            {"mod:m.c": [routine]},
        )

    def test_module_rollup_rejects_unknown_or_duplicate_function_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "owner_id"):
            _group_functions_by_module(
                [_Node("code:f", owner_id="mod:missing")],
                [_Module()],
            )
        with self.assertRaisesRegex(ValueError, "duplicate function IDs"):
            _group_functions_by_module(
                [_Node("code:f"), _Node("code:f")],
                [_Module()],
            )

    async def test_module_rollup_preserves_each_summary_byte_identically(self) -> None:
        handoff = AnalysisHandoff(PrioritySemaphore(8))
        first = _Node("code:a", summary="첫째 요약")
        second = _Node("code:z", summary="둘째 요약\n두 번째 줄")
        handoff.expect([first.id, second.id])
        handoff.publish_summary(second)
        handoff.publish_summary(first)
        handoff.resolve(first.id)
        handoff.resolve(second.id)
        handoff.close()

        result = await build_module_summary_rollups_step(
            {"modules": [_Module()], "code_nodes": [second, first]},
            config={
                "configurable": {
                    "analysis_handoff": handoff,
                    "neo_client": None,
                }
            },
        )

        module = result["modules"][0]
        self.assertEqual(("code:a", "code:z"), module.summary_rollup_function_ids)
        self.assertEqual(
            ("첫째 요약", "둘째 요약\n두 번째 줄"),
            module.summary_rollup_summaries,
        )

    async def test_module_summary_starts_before_step7_finishes(self) -> None:
        handoff = AnalysisHandoff(PrioritySemaphore(8))
        timeline: list[str] = []
        node = _Node("code:f")
        step7_done = asyncio.Event()

        async def fake_analyze_code_step(state: dict, config=None) -> dict:
            """실제 step7 의 신호 순서 — #1 확정이 노드 완료보다 한참 이르다."""
            handoff.expect(["code:f"])
            node.summary = "확정된 요약"
            handoff.publish_summary(node)
            timeline.append("step7:summary_published")
            await asyncio.sleep(0.05)          # 남은 rules 콜(기준선 100초 구간)
            handoff.resolve("code:f")
            handoff.close()
            timeline.append("step7:done")
            step7_done.set()
            return {"analysis_contexts": {}}

        seen_summaries: list[str] = []

        def observed_grouping(code_nodes, modules):
            timeline.append("postprocess:rollup")
            seen_summaries.extend(
                node.summary for node in code_nodes if node.summary
            )
            self.assertFalse(
                step7_done.is_set(),
                "후처리 rollup이 step7 완료 뒤에야 시작됐다 — 겹치지 않았다",
            )
            return _group_functions_by_module(code_nodes, modules)

        state = {
            "run": None,
            "modules": [_Module()],
            "code_nodes": [_Node("code:f")],     # state 사본 — 확정본은 handoff 가 갖는다
        }
        target = "pipeline.steps.step8_post_processing.build_module_summary_rollups_step"
        with mock.patch(f"{target}._group_functions_by_module", side_effect=observed_grouping):
            strategy = _Strategy(steps=((fake_analyze_code_step, build_module_summary_rollups_step),))
            app = build_langgraph_workflow(strategy, checkpointer=MemorySaver())
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": "t-overlap",
                    "strategy": strategy,
                    "analysis_handoff": handoff,
                    "cost_budget": None,
                    "neo_client": None,
                }
            }
            await asyncio.wait_for(app.ainvoke(state, config=config), timeout=5)

        self.assertEqual(
            timeline[0], "step7:summary_published",
            "후처리가 #1 확정 전에 시작했다 — 부분 입력으로 프롬프트가 만들어진다",
        )
        self.assertIn("postprocess:rollup", timeline)
        self.assertLess(
            timeline.index("postprocess:rollup"), timeline.index("step7:done"),
            "후처리가 step7 완료 뒤에 시작했다 — 배리어가 남아 있다",
        )
        self.assertIn(
            "확정된 요약", seen_summaries,
            "후처리가 state 의 미확정 사본을 읽었다 — merge 계약 위반",
        )

    async def test_step7_failure_does_not_hang_postprocessing(self) -> None:
        """게이트가 안 열리면 superstep 이 영원히 안 끝난다 — 가장 비싼 실패 경로."""
        handoff = AnalysisHandoff(PrioritySemaphore(8))

        async def exploding_step7(state: dict, config=None) -> dict:
            handoff.expect(["code:f"])
            try:
                raise RuntimeError("step7 폭발")
            except BaseException as error:
                handoff.abort(error)
                raise

        strategy = _Strategy(steps=((exploding_step7, build_module_summary_rollups_step),))
        app = build_langgraph_workflow(strategy, checkpointer=MemorySaver())
        config: RunnableConfig = {
            "configurable": {
                "thread_id": "t-abort", "strategy": strategy,
                "analysis_handoff": handoff, "cost_budget": None, "neo_client": None,
            }
        }
        with self.assertRaises(RuntimeError):
            await asyncio.wait_for(
                app.ainvoke({"run": None, "modules": [_Module()], "code_nodes": []},
                            config=config),
                timeout=5,
            )

    async def test_module_summary_is_quarantined_when_member_summary_is_missing(self) -> None:
        handoff = AnalysisHandoff(PrioritySemaphore(8))
        available = _Node("code:ok", summary="검증된 멤버 요약")
        missing = _Node("code:missing", summary=None)
        handoff.expect([available.id, missing.id])
        handoff.publish_summary(available)
        handoff.publish_summary(missing)
        handoff.resolve(available.id)
        handoff.resolve(missing.id)
        handoff.close()
        result = await build_module_summary_rollups_step(
                {
                    "run": None,
                    "modules": [_Module()],
                    "code_nodes": [available, missing],
                },
                config={
                    "configurable": {
                        "strategy": _Strategy(steps=()),
                        "analysis_handoff": handoff,
                        "cost_budget": None,
                        "neo_client": None,
                    }
                },
            )

        self.assertEqual((), result["modules"][0].summary_rollup_function_ids)
        self.assertEqual((), result["modules"][0].summary_rollup_summaries)


if __name__ == "__main__":
    unittest.main()
