import unittest
from types import SimpleNamespace

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step6_build_context.build_context_step import build_context_step
from pipeline.steps.step6_build_context.framework_context_builder import FrameworkContextBuilder
from pipeline.steps.step6_build_context.control_subtree import build_control_subtrees
from pipeline.steps.step7_analyze_code.framework_per_node import framework_analysis_targets


def _node(node_id: str, label: str, start: int, end: int, parent_id: str | None) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=node_id,
        file_path="sample.c",
        start_line=start,
        end_line=end,
        label=label,
        name=node_id,
        code_text="\n".join(f"line {line}" for line in range(start, end + 1)),
        owner_id="sample.c",
        parent_id=parent_id,
    )


def _tree() -> list[CodeNode]:
    return [
        _node("fn", "FUNCTION", 100, 140, None),
        _node("fn:IF@103", "IF", 103, 120, "fn"),
        _node("fn:IF@103:ELSE@112", "ELSE", 112, 120, "fn:IF@103"),
        _node("fn:LOOP@125", "LOOP", 125, 135, "fn"),
    ]


class ControlSubtreeTest(unittest.TestCase):
    def test_uses_routine_relative_coordinates_and_nesting(self):
        trees = build_control_subtrees(_tree())

        self.assertEqual(list(trees), ["fn"])
        self.assertEqual(
            [(node.ntype, node.start, node.end) for node in trees["fn"]],
            [("IF", 4, 21), ("LOOP", 26, 36)],
        )
        self.assertEqual(
            [(node.ntype, node.start, node.end) for node in trees["fn"][0].children],
            [("ELSE", 13, 21)],
        )

    def test_rejects_control_range_outside_routine(self):
        nodes = _tree()
        nodes[1].end_line = 141
        with self.assertRaisesRegex(ValueError, "control range escapes routine"):
            build_control_subtrees(nodes)

    def test_control_nodes_under_a_non_control_wrapper_still_reach_the_routine(self):
        """비제어 구조 노드(PL/SQL ``TRIGGER_BLOCK``·``DECLARE``)를 건너뛰고 소유자를 찾는다.

        ``build_statement_facts`` 는 이미 부모 사슬을 올라가 소유 routine 을 찾는데
        (``control_subtree.py`` owning_routine), 제어 트리만 ``parent_id`` 를 직접 키로 써서
        **같은 트리를 다르게 읽고 있었다.** 그 결과 rwis 트리거 ``TRG_INS_RDITAG_TB`` 는
        IF 2개를 가지고도 분기표가 비어 규칙 0건으로 조용히 통과했다(헌법 IV, spec 118).
        """
        nodes = [
            _node("trg", "TRIGGER", 1, 24, None),
            _node("trg:BLOCK@6", "TRIGGER_BLOCK", 6, 24, "trg"),
            _node("trg:BLOCK@6:IF@10", "IF", 10, 12, "trg:BLOCK@6"),
            _node("trg:BLOCK@6:IF@16", "IF", 16, 22, "trg:BLOCK@6"),
            _node("trg:BLOCK@6:IF@16:ELSE@19", "ELSE", 19, 22, "trg:BLOCK@6:IF@16"),
        ]

        trees = build_control_subtrees(nodes)

        self.assertEqual(list(trees), ["trg"])
        self.assertEqual(
            [(node.ntype, node.start, node.end) for node in trees["trg"]],
            [("IF", 10, 12), ("IF", 16, 22)],
        )
        self.assertEqual(
            [(node.ntype, node.start, node.end) for node in trees["trg"][1].children],
            [("ELSE", 19, 22)],
        )


class ControlSubtreeWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_attaches_only_to_routine(self):
        nodes = _tree()
        strategy = SimpleNamespace(
            context_builder=FrameworkContextBuilder(),
            analysis_targets=framework_analysis_targets,
        )

        result = await build_context_step(
            {"code_nodes": nodes},
            {"configurable": {"strategy": strategy}},
        )

        contexts = result["analysis_contexts"]
        subtree = contexts["fn"].extra["control_subtree"]
        self.assertEqual(
            [(node.ntype, node.start, node.end) for node in subtree],
            [("IF", 4, 21), ("LOOP", 26, 36)],
        )
        self.assertNotIn("control_subtree", contexts["fn:IF@103"].extra)
