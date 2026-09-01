"""step7 dataflow 스케줄 계약 — 의존 종류별 해제 시점과 투입 우선순위.

CALLS와 PARENT_OF 의존은 하위 summary 확정 신호로 조기 해제된다(rules 완료 대기 X).
투입 순서는 critical-path rank
내림차순이다. 전부 산출물 불변인 스케줄 결정이므로 여기서는 순서·해제 시점만 검증한다.
"""
from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from unittest import mock

from pipeline.execution.analysis_handoff import AnalysisHandoff
from shared.concurrency.priority_semaphore import PrioritySemaphore
from pipeline.meaning.framework_per_node import (
    _build_dependency_map,
    _dispatch_ranks,
    run_framework_per_node_analysis,
)
from pipeline.meaning.batch_analyze import _analyze_batch
from pipeline.meaning.batch_planner import Batch
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import BatchSummaryItem
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from graph.schema.graph_nodes import CodeNode
from tests.pipeline.semantic_frame_fixtures import semantic_frame_for_node


@dataclass
class _Node:
    id: str
    start_line: int = 1
    end_line: int = 10
    label: str = "FUNCTION"


@dataclass
class _Ctx:
    id: str
    callees: tuple[str, ...] = ()
    summary: str | None = None
    is_in_cycle: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class _CallEdge:
    caller_id: str
    callee_id: str


class DependencyMapTest(unittest.TestCase):
    def test_excludes_outside_and_self(self):
        contexts = {key: _Ctx(key) for key in ("a", "b", "c")}
        deps = _build_dependency_map(
            [("a", "b"), ("a", "a"), ("a", "외부"), ("b", "c")], contexts
        )
        self.assertEqual(deps["a"], {"b"})     # 정상 의존만
        self.assertEqual(deps["b"], {"c"})
        self.assertEqual(deps["c"], set())


class DispatchRanksTest(unittest.TestCase):
    def test_chain_bottom_ranks_higher_and_size_breaks_ties(self):
        # a → b → c 사슬: 아래쪽(c)이 최우선. 독립 노드는 크기가 큰 쪽이 우선.
        contexts = {k: _Ctx(k) for k in ("a", "b", "c", "big", "small")}
        code_by_id = {
            "a": _Node("a"), "b": _Node("b"), "c": _Node("c"),
            "big": _Node("big", 1, 500), "small": _Node("small", 1, 5),
        }
        summary_deps = _build_dependency_map([("a", "b"), ("b", "c")], contexts)
        ranks = _dispatch_ranks(contexts, code_by_id, summary_deps)
        self.assertGreater(ranks["c"], ranks["b"])
        self.assertGreater(ranks["b"], ranks["a"])
        self.assertGreater(ranks["big"], ranks["small"])


class EarlySummaryReleaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_analyzed_code_nodes_as_an_explicit_state_update(self):
        node = CodeNode(
            id="function:a", name="a", signature="a()", file_path="a.c",
            start_line=1, end_line=2, label="FUNCTION",
        )
        contexts = {node.id: _Ctx(node.id)}
        code_by_id = {node.id: node}
        state = {
            "code_nodes": [node],
            "calls_edges": [],
            "parent_of_edges": [],
        }

        async def _fake_three_split(code_id, contexts, code_by_id, strategy, **kwargs):
            analyzed = code_by_id[code_id]
            analyzed.meaning_status = "sufficient"
            on_summary_ready = kwargs.get("on_summary_ready")
            if on_summary_ready is not None:
                on_summary_ready(code_id)
            return analyzed

        async def _fake_finalize(result, stream):
            from pipeline.meaning.rule.rule_graph import RuleGraphResult
            return RuleGraphResult()

        target = "pipeline.meaning.framework_per_node"
        with mock.patch(f"{target}.analyze_code_three_split", _fake_three_split), \
             mock.patch(f"{target}.finalize_and_emit_code_result", _fake_finalize):
            from pipeline.execution.pause_controller import PauseController
            result = await run_framework_per_node_analysis(
                state=state, contexts=contexts, code_by_id=code_by_id,
                strategy=mock.Mock(prompts_dir="pipeline/meaning/framework", name="framework"),
                cost_budget=None, stream=None, table_descriptions={},
                pause=PauseController(), source_cache=mock.Mock(),
                handoff=AnalysisHandoff(PrioritySemaphore(64)),
            )

        self.assertEqual("sufficient", result["code_nodes"][0].meaning_status)

    async def test_first_summary_warms_without_blocking_other_node_work(self):
        contexts = {key: _Ctx(key) for key in ("a", "b")}
        code_by_id = {key: _Node(key) for key in contexts}
        state = {
            "code_nodes": list(code_by_id.values()),
            "calls_edges": [],
            "parent_of_edges": [],
        }
        events: list[str] = []
        started: set[str] = set()
        all_nodes_started = asyncio.Event()

        async def _fake_three_split(code_id, contexts, code_by_id, strategy, **kwargs):
            started.add(code_id)
            events.append(f"node:{code_id}")
            if len(started) == 2:
                all_nodes_started.set()
            warm_event = kwargs["summary_warm_event"]
            if kwargs["is_summary_warmup"]:
                events.append("warm_summary:started")
                await asyncio.wait_for(all_nodes_started.wait(), timeout=1)
                events.append("warm_summary:done")
                warm_event.set()
            else:
                events.append("other_work:started")
                await asyncio.wait_for(warm_event.wait(), timeout=1)
                events.append("other_summary:started")
            on_summary_ready = kwargs.get("on_summary_ready")
            if on_summary_ready is not None:
                on_summary_ready(code_id)
            return code_by_id[code_id]

        async def _fake_finalize(result, stream):
            from pipeline.meaning.rule.rule_graph import RuleGraphResult
            return RuleGraphResult()

        target = "pipeline.meaning.framework_per_node"
        with mock.patch(f"{target}.analyze_code_three_split", _fake_three_split), \
             mock.patch(f"{target}.finalize_and_emit_code_result", _fake_finalize):
            from pipeline.execution.pause_controller import PauseController
            await run_framework_per_node_analysis(
                state=state, contexts=contexts, code_by_id=code_by_id,
                strategy=mock.Mock(prompts_dir="pipeline/meaning/framework", name="framework"),
                cost_budget=None, stream=None,
                table_descriptions={}, pause=PauseController(),
                source_cache=mock.Mock(),
                handoff=AnalysisHandoff(PrioritySemaphore(64)),
            )

        self.assertLess(
            events.index("other_work:started"), events.index("warm_summary:done")
        )
        self.assertLess(
            events.index("warm_summary:done"), events.index("other_summary:started")
        )

    async def test_caller_starts_after_callee_summary_not_full_completion(self):
        """caller(CALLS 의존)는 callee 의 rules 완료가 아니라 summary 확정에 풀린다."""
        contexts = {
            "caller": _Ctx("caller", callees=("callee",)),
            "callee": _Ctx("callee"),
        }
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

        async def _fake_finalize(result, stream):
            from pipeline.meaning.rule.rule_graph import (
                RuleGraphResult,
            )
            if result.id == "caller":
                callee_rules_hold.set()   # caller 가 끝났으면 callee 를 풀어 완주시킨다
            return RuleGraphResult()

        target = "pipeline.meaning.framework_per_node"
        with mock.patch(f"{target}.analyze_code_three_split", _fake_three_split), \
             mock.patch(f"{target}.finalize_and_emit_code_result", _fake_finalize):
            from pipeline.execution.pause_controller import PauseController
            result = await run_framework_per_node_analysis(
                state=state, contexts=contexts, code_by_id=code_by_id,
                strategy=mock.Mock(prompts_dir="pipeline/meaning/framework", name="framework"),
                cost_budget=None, stream=None,
                table_descriptions={},
                pause=PauseController(), source_cache=mock.Mock(),
                handoff=AnalysisHandoff(PrioritySemaphore(64)),
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

    async def test_parent_starts_after_child_summary_not_full_completion(self):
        """PARENT_OF uses the same summary-only release contract as CALLS."""
        contexts = {"parent": _Ctx("parent"), "child": _Ctx("child")}
        code_by_id = {"parent": _Node("parent"), "child": _Node("child")}
        parent_edge = mock.Mock(parent_id="parent", child_id="child")
        state = {
            "code_nodes": list(code_by_id.values()),
            "calls_edges": [],
            "parent_of_edges": [parent_edge],
        }
        events: list[str] = []
        child_tail_hold = asyncio.Event()

        async def _fake_three_split(code_id, contexts, code_by_id, strategy, **kwargs):
            events.append(f"start:{code_id}")
            contexts[code_id].summary = f"{code_id} summary"
            on_summary_ready = kwargs.get("on_summary_ready")
            if on_summary_ready is not None:
                on_summary_ready(code_id)
            events.append(f"summary:{code_id}")
            if code_id == "child":
                await child_tail_hold.wait()
            events.append(f"done:{code_id}")
            return code_by_id[code_id]

        async def _fake_finalize(result, stream):
            from pipeline.meaning.rule.rule_graph import (
                RuleGraphResult,
            )
            if result.id == "parent":
                child_tail_hold.set()
            return RuleGraphResult()

        target = "pipeline.meaning.framework_per_node"
        with mock.patch(f"{target}.analyze_code_three_split", _fake_three_split), \
             mock.patch(f"{target}.finalize_and_emit_code_result", _fake_finalize):
            from pipeline.execution.pause_controller import PauseController
            await run_framework_per_node_analysis(
                state=state, contexts=contexts, code_by_id=code_by_id,
                strategy=mock.Mock(prompts_dir="pipeline/meaning/framework", name="framework"),
                cost_budget=None, stream=None,
                table_descriptions={},
                pause=PauseController(), source_cache=mock.Mock(),
                handoff=AnalysisHandoff(PrioritySemaphore(64)),
            )

        self.assertLess(events.index("summary:child"), events.index("start:parent"))
        self.assertLess(events.index("start:parent"), events.index("done:child"))


class BatchEarlySummaryReleaseTest(unittest.IsolatedAsyncioTestCase):
    async def test_dbms_summary_is_published_while_table_concern_is_running(self):
        node = CodeNode(
            id="n", signature="n()", file_path="sample.sql",
            start_line=1, end_line=3, label="PROCEDURE",
        )
        ctx = AnalysisContext(
            id="n", signature="n()", file_path="sample.sql",
            start_line=1, end_line=3,
            direct_reads=("db:APP.ORDERS",),
            data_object_usages=(DataObjectUsageEvidence(
                source_id="n",
                raw_reference="db:APP.ORDERS",
                name="ORDERS",
                access="READ",
                evidence_file="sample.sql",
                evidence_line=2,
                producer="linked_edges",
                table_id="db:APP.ORDERS",
                resolution="exact",
            ),),
        )
        summary_ready = asyncio.Event()
        table_hold = asyncio.Event()

        async def _fake_collect(**kwargs):
            if kwargs["concern"] == "summary":
                return {
                    "n": BatchSummaryItem(
                        node_key="1-3", summary="업무 처리를 수행한다",
                        stereotype="DataQuery", evidence_status="sufficient",
                        missing_context=[],
                        semantic_frame=semantic_frame_for_node(
                            node, meaning="업무 처리를 수행한다",
                        ),
                    )
                }
            await table_hold.wait()
            return {}

        def _on_summaries_ready(node_ids):
            self.assertEqual(["n"], node_ids)
            summary_ready.set()

        target = "pipeline.meaning.batch_analyze"
        strategy = mock.Mock()
        strategy.prompts_dir = "pipeline/meaning/dbms"
        strategy.name = "dbms"
        with mock.patch(f"{target}._collect_keyed_concern", _fake_collect):
            task = asyncio.create_task(_analyze_batch(
                Batch(parent_id=None, node_ids=("n",)),
                {"n": ctx}, {"n": node},
                strategy,
                None, {}, mock.Mock(), PrioritySemaphore(64),
                set(), set(), _on_summaries_ready,
            ))
            try:
                ready_waiter = asyncio.create_task(summary_ready.wait())
                finished, _ = await asyncio.wait(
                    {task, ready_waiter}, timeout=1,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in finished:
                    task.result()
                self.assertIn(ready_waiter, finished, "summary-ready signal timed out")
                self.assertEqual("업무 처리를 수행한다", node.summary)
                self.assertEqual(
                    "accepted",
                    ctx.extra["summary_status"],
                )
                self.assertFalse(task.done(), "table-insight tail should still be running")
            finally:
                if 'ready_waiter' in locals() and not ready_waiter.done():
                    ready_waiter.cancel()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
