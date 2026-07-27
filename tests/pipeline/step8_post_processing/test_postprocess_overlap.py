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
from pipeline.steps.step8_post_processing.summarize_module_step import (
    _select_analyzed_code_nodes,
    summarize_module_step,
)
from shared.concurrency.priority_semaphore import PrioritySemaphore


@dataclass
class _Node:
    id: str
    label: str = "FUNCTION"
    file_path: str = "m.c"
    start_line: int = 1
    end_line: int = 9
    signature: str = "int f(void)"
    summary: Optional[str] = None
    logical_name: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Module:
    id: str = "mod:m.c"
    name: str = "m.c"
    file_path: str = "m.c"
    start_line: int = 0
    end_line: int = 0
    summary: Optional[str] = None
    stereotype: Optional[str] = None
    logical_name: Optional[str] = None


@dataclass(frozen=True)
class _Strategy:
    steps: tuple
    name: str = "framework"
    prompts_dir: str = "llm/prompts/framework"
    analysis_targets: Callable = lambda nodes: {
        node.id for node in nodes if node.label == "FUNCTION"
    }


class PostprocessOverlapTest(unittest.IsolatedAsyncioTestCase):
    def test_module_summary_uses_strategy_analysis_targets(self) -> None:
        routine = _Node("code:f", label="FUNCTION")
        control = _Node("code:f:IF@2", label="IF")
        strategy = _Strategy(steps=())

        self.assertEqual(
            _select_analyzed_code_nodes(strategy, [routine, control]),
            [routine],
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
            node.logical_name = "확정된 논리명"
            handoff.publish_summary(node)
            timeline.append("step7:summary_published")
            await asyncio.sleep(0.05)          # 남은 rules 콜(기준선 100초 구간)
            handoff.resolve("code:f")
            handoff.close()
            timeline.append("step7:done")
            step7_done.set()
            return {"analysis_contexts": {}}

        seen_summaries: list[str] = []

        async def fake_rule(**kwargs) -> object:
            timeline.append("postprocess:llm_call")
            seen_summaries.append(kwargs["user_values"]["member_code_nodes"])
            self.assertFalse(
                step7_done.is_set(),
                "후처리 LLM 콜이 step7 완료 뒤에야 나갔다 — 겹치지 않았다",
            )
            return mock.Mock(summary="모듈 요약", stereotype="Service", logical_name="모듈")

        state = {
            "run": None,
            "modules": [_Module()],
            "code_nodes": [_Node("code:f")],     # state 사본 — 확정본은 handoff 가 갖는다
        }
        target = "pipeline.steps.step8_post_processing.summarize_module_step"
        with mock.patch(f"{target}.run_yaml_rule", side_effect=fake_rule), \
             mock.patch(f"{target}.upsert_modules", mock.AsyncMock()), \
             mock.patch(f"{target}.emit_nodes", mock.AsyncMock()):
            strategy = _Strategy(steps=((fake_analyze_code_step, summarize_module_step),))
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
        self.assertIn("postprocess:llm_call", timeline)
        self.assertLess(
            timeline.index("postprocess:llm_call"), timeline.index("step7:done"),
            "후처리가 step7 완료 뒤에 시작했다 — 배리어가 남아 있다",
        )
        self.assertIn(
            "확정된 요약", seen_summaries[0],
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

        strategy = _Strategy(steps=((exploding_step7, summarize_module_step),))
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


if __name__ == "__main__":
    unittest.main()
