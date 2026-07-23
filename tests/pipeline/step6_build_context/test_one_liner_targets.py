"""사전 압축(one_liner) 대상 계약 — **읽힐 수 있는 노드만** 압축한다.

one_liner 는 렌더 시 summary 가 없을 때의 대타다. 소비처는 셋(callee 요약·자식 치환·
조상 골격)이고 각각 관문이 있다: 자식 치환은 ``ctx.extra["child_ids"]``, 조상 골격은
``ctx.extra["parent_id"]``, callee 요약은 summary 가 없는 경우(=순환 내부)다.
관문에 안 걸리는 노드는 압축해도 읽힐 수 없다.

spec 083 이 "분석 대상 노드만"까지 좁혔고, 여기서 "읽힐 수 있는 노드만"으로 대체한다.
엔진 이름으로 분기하지 않는다 — 부모-자식을 채우는 strategy 는 그대로 살고, 안 채우는
strategy 는 관문이 닫혀 자동으로 순환만 남는다.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.steps.step6_build_context.build_context_step import (
    _one_liner_readable_ids,
    build_context_step,
)
from pipeline.steps.step6_build_context.framework_context_builder import FrameworkContextBuilder
from pipeline.steps.step7_analyze_code.batch_analyze import dbms_analysis_targets
from pipeline.steps.step7_analyze_code.framework_per_node import framework_analysis_targets


def _node(node_id: str, label: str, lines: int, parent_id: str | None = None) -> CodeNode:
    # 한 줄을 넉넉히 길게 만들어 토큰 임계(1000)를 확실히 넘긴다.
    body = "\n".join(f"    result_{i} = compute_value(alpha, beta, gamma, delta);" for i in range(lines))
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path="sample.c",
        start_line=1,
        end_line=lines,
        label=label,
        name=node_id,
        code_text=body,
        owner_id="sample.c",
        parent_id=parent_id,
    )


class AnalysisTargetsTest(unittest.TestCase):
    def test_framework_targets_are_routines_only(self):
        nodes = [_node("fn", "FUNCTION", 5), _node("fn:IF@2", "IF", 3, "fn")]
        self.assertEqual(framework_analysis_targets(nodes), {"fn"})

    def test_dbms_targets_keep_non_routine_ranges(self):
        # DBMS 는 range 자체가 분석 단위 — 제어문도 대상에 남아야 한다.
        nodes = [_node("proc", "PROCEDURE", 5), _node("proc:IF@2", "IF", 3, "proc")]
        targets = dbms_analysis_targets(nodes)
        self.assertIn("proc", targets)
        self.assertIn("proc:IF@2", targets)


def _ctx(ctx_id: str, *, callees: tuple[str, ...] = (), **extra) -> AnalysisContext:
    return AnalysisContext(
        id=ctx_id, signature=ctx_id, file_path="sample.c",
        start_line=1, end_line=2, callees=callees, extra=dict(extra),
    )


class OneLinerReadableSlotTest(unittest.TestCase):
    """소비처 세 곳의 관문에서 역산한다 — 엔진 이름이 아니라 데이터가 정한다."""

    def test_parent_and_child_references_are_readable(self):
        contexts = {
            "proc": _ctx("proc", child_ids=["proc:IF@2"]),
            "proc:IF@2": _ctx("proc:IF@2", parent_id="proc"),
        }
        self.assertEqual({"proc", "proc:IF@2"}, _one_liner_readable_ids(contexts))

    def test_cycle_members_are_readable(self):
        # 순환 내부는 callee summary 가 제때 없을 수 있는 유일한 구조적 경우다.
        contexts = {
            "a": _ctx("a", callees=("b",)),
            "b": _ctx("b", callees=("a",)),
            "c": _ctx("c", callees=("a",)),
        }
        readable = _one_liner_readable_ids(contexts)
        self.assertEqual({"a", "b"}, readable)
        self.assertNotIn("c", readable, "순환 밖인데 읽힐 자리로 셌다")

    def test_flat_call_chain_has_no_readable_slot(self):
        # 부모-자식도 순환도 없으면 callee 는 항상 summary 를 먼저 받는다 → 대타 불필요.
        contexts = {"caller": _ctx("caller", callees=("callee",)), "callee": _ctx("callee")}
        self.assertEqual(set(), _one_liner_readable_ids(contexts))


class OneLinerTargetScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_readable_slot_means_no_compression(self):
        """읽힐 자리가 없으면 큰 분석 대상이라도 압축하지 않는다.

        spec 083 은 "분석 대상이면 압축"까지 좁혔고, 여기서 "읽힐 수 있으면 압축"으로
        더 좁힌다. Framework 는 ctx.extra 의 부모-자식을 안 채우므로 자식 치환·조상 골격이
        도달 불가이고, 순환이 없으면 callee 요약도 항상 summary 를 먼저 본다.
        """
        nodes = [_node("fn", "FUNCTION", 400), _node("fn:IF@2", "IF", 300, "fn")]
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )
        target_path = (
            "pipeline.steps.step6_build_context.build_context_step.compute_one_liner"
        )
        with mock.patch(target_path, new=mock.AsyncMock(return_value="요약")) as compressed:
            result = await build_context_step(
                {"code_nodes": nodes},
                {"configurable": {"strategy": strategy}},
            )

        contexts = result["analysis_contexts"]
        # 둘 다 임계를 넘겼는지 먼저 확인 — 아니면 이 테스트가 무의미해진다.
        self.assertGreater(contexts["fn"].token_count, 1000)
        self.assertGreater(contexts["fn:IF@2"].token_count, 1000)
        self.assertEqual([], [call.args[0].id for call in compressed.await_args_list])
        self.assertIsNone(contexts["fn"].one_liner)
        self.assertIsNone(contexts["fn:IF@2"].one_liner)


if __name__ == "__main__":
    unittest.main()
