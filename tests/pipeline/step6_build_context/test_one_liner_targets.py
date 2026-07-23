"""사전 압축(one_liner) 대상 계약 — 분석 대상 노드만 압축한다 (spec 083).

one_liner 는 렌더 시 summary 가 없을 때의 대타이고, 세 소비처(callee 요약·자식 치환·
조상 골격)가 모두 분석 컨텍스트에 있는 id 만 조회한다. 따라서 분석 대상 밖 노드는
압축해도 읽힐 수 없다. 엔진과 step6 이 같은 답을 보는지를 여기서 고정한다.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step6_build_context.build_context_step import build_context_step
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


class OneLinerTargetScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_compresses_target_only(self):
        """큰 제어문 노드는 압축 호출 대상이 아니다 — 읽힐 수 없는 산출물이라서."""
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
        compressed_ids = [call.args[0].id for call in compressed.await_args_list]
        self.assertEqual(compressed_ids, ["fn"])
        self.assertIsNone(contexts["fn:IF@2"].one_liner)


if __name__ == "__main__":
    unittest.main()
