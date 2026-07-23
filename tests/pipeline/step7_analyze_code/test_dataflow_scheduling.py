"""step7 dataflow 스케줄 계약 — 의존 종류별 해제 시점과 투입 우선순위.

CALLS 의존은 callee 의 summary 확정 신호로 조기 해제되고(rules 완료 대기 X),
PARENT_OF 의존은 자식 전체 완료를 기다린다. 투입 순서는 critical-path rank
내림차순이다. 전부 산출물 불변인 스케줄 결정이므로 여기서는 순서·해제 시점만 검증한다.
"""
from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from unittest import mock

from pipeline.steps.step7_analyze_code.framework_per_node import (
    _build_dependency_map,
    _dispatch_ranks,
    run_framework_per_node_analysis,
)


@dataclass
class _Node:
    id: str
    start_line: int = 1
    end_line: int = 10
    label: str = "FUNCTION"


@dataclass
class _Ctx:
    summary: str | None = None
    is_in_cycle: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class _CallEdge:
    caller_id: str
    callee_id: str


class DependencyMapTest(unittest.TestCase):
    def test_excludes_outside_self_and_same_scc(self):
        contexts = {"a": _Ctx(), "b": _Ctx(), "c": _Ctx()}
        cycles = {"b": "scc1", "c": "scc1"}   # b·c 는 같은 SCC
        deps = _build_dependency_map(
            [("a", "b"), ("a", "a"), ("a", "외부"), ("b", "c")], contexts, cycles
        )
        self.assertEqual(deps["a"], {"b"})     # 정상 의존만
        self.assertEqual(deps["b"], set())     # 같은 SCC 내부 의존은 끊김
        self.assertEqual(deps["c"], set())


class DispatchRanksTest(unittest.TestCase):
    def test_chain_bottom_ranks_higher_and_size_breaks_ties(self):
        # a → b → c 사슬: 아래쪽(c)이 최우선. 독립 노드는 크기가 큰 쪽이 우선.
        contexts = {k: _Ctx() for k in ("a", "b", "c", "big", "small")}
        code_by_id = {
            "a": _Node("a"), "b": _Node("b"), "c": _Node("c"),
            "big": _Node("big", 1, 500), "small": _Node("small", 1, 5),
        }
        summary_deps = _build_dependency_map([("a", "b"), ("b", "c")], contexts, {})
        full_deps = {fid: set() for fid in contexts}
        ranks = _dispatch_ranks(contexts, code_by_id, summary_deps, full_deps)
        self.assertGreater(ranks["c"], ranks["b"])
        self.assertGreater(ranks["b"], ranks["a"])
        self.assertGreater(ranks["big"], ranks["small"])


class EarlySummaryReleaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_caller_starts_after_callee_summary_not_full_completion(self):
        """caller(CALLS 의존)는 callee 의 rules 완료가 아니라 summary 확정에 풀린다."""
        contexts = {"caller": _Ctx(), "callee": _Ctx()}
        code_by_id = {"caller": _Node("caller"), "callee": _Node("callee")}
        state = {
            "code_nodes": list(code_by_id.values()),
            "calls_edges": [_CallEdge("caller", "callee")],
            "parent_of_edges": [],
        }
        events: list[str] = []
        callee_rules_hold = asyncio.Event()   # callee 의 #2/#3 를 인위로 붙잡는다

        async def _fake_three_split(code_id, contexts, code_by_id, strategy, **kwargs):
            events.append(f"start:{code_id}")
            on_summary_ready = kwargs.get("on_summary_ready")
            contexts[code_id].summary = f"{code_id} 요약"
            if on_summary_ready is not None:
                on_summary_ready(code_id)
            events.append(f"summary:{code_id}")
            if code_id == "callee":
                await callee_rules_hold.wait()   # rules 단계가 오래 걸리는 상황
            events.append(f"done:{code_id}")
            return code_by_id[code_id]

        async def _fake_persist(result, neo, stream):
            from pipeline.steps.step7_analyze_code.example_mapping_builder import (
                ExampleMappingResult,
            )
            if result.id == "caller":
                callee_rules_hold.set()   # caller 가 끝났으면 callee 를 풀어 완주시킨다
            return ExampleMappingResult()

        target = "pipeline.steps.step7_analyze_code.framework_per_node"
        with mock.patch(f"{target}.analyze_code_three_split", _fake_three_split), \
             mock.patch(f"{target}.run_summary", mock.AsyncMock(return_value=None)), \
             mock.patch(f"{target}._persist_and_emit_code", _fake_persist):
            from pipeline.execution.pause_controller import PauseController
            result = await run_framework_per_node_analysis(
                state=state, contexts=contexts, code_by_id=code_by_id,
                strategy=mock.Mock(prompts_dir="llm/prompts/framework", name="framework"),
                cost_budget=None, neo=None, stream=None,
                codebase_notes="", module_intents={}, table_descriptions={},
                pause=PauseController(), source_cache=mock.Mock(),
            )

        # 핵심 계약: caller 는 callee 의 done 이전(=summary 확정 후)에 시작해야 한다.
        self.assertIn("start:caller", events)
        self.assertLess(
            events.index("summary:callee"), events.index("start:caller"),
            "caller 가 callee summary 확정 전에 시작됨 — 의존 위반",
        )
        self.assertLess(
            events.index("start:caller"), events.index("done:callee"),
            "caller 가 callee 전체 완료까지 기다림 — 조기 해제가 작동하지 않음",
        )
        self.assertEqual(len(result["analysis_contexts"]), 2)


if __name__ == "__main__":
    unittest.main()
