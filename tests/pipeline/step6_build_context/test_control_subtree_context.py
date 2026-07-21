import unittest
from types import SimpleNamespace

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step6_build_context.build_context_step import build_context_step
from pipeline.steps.step6_build_context.framework_context_builder import FrameworkContextBuilder
from pipeline.steps.step6_build_context.control_subtree import build_control_subtrees
from pipeline.steps.step7_analyze_code.rules_fallback import build_fragments


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


class ControlSubtreeWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_attaches_only_to_routine_and_drives_nonempty_fragments(self):
        nodes = _tree()
        strategy = SimpleNamespace(context_builder=FrameworkContextBuilder())

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
        self.assertGreater(
            len(build_fragments(nodes[0].code_text, subtree, budget_tokens=1)),
            0,
        )
