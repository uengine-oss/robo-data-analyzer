from __future__ import annotations

import asyncio
import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step4_linking.link_calls.call_linker import CallLinker


def _node(
    node_id: str,
    signature: str,
    *,
    label: str,
    parent_id: str | None = None,
) -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=signature,
        file_path="neutral.sql",
        start_line=1,
        end_line=1,
        label=label,
        parent_id=parent_id,
        package="APP",
    )


class DbmsCallLinkerTest(unittest.TestCase):
    def test_project_definition_wins_over_builtin_looking_name(self) -> None:
        nodes = [
            _node("routine:custom", "DBMS_CUSTOM", label="PROCEDURE"),
            _node("call:custom", "DBMS_CUSTOM", label="CALL", parent_id="routine:custom"),
        ]

        edges = asyncio.run(CallLinker().link(nodes))

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].scope, "internal")
        self.assertEqual(edges[0].callee_id, "routine:custom")

    def test_exact_registered_package_call_is_dropped(self) -> None:
        nodes = [
            _node("routine:caller", "CALLER", label="PROCEDURE"),
            _node(
                "call:builtin",
                "DBMS_OUTPUT.PUT_LINE",
                label="CALL",
                parent_id="routine:caller",
            ),
        ]

        self.assertEqual(asyncio.run(CallLinker().link(nodes)), [])

    def test_prefix_lookalikes_remain_external(self) -> None:
        nodes = [
            _node("routine:caller", "CALLER", label="PROCEDURE"),
            _node("call:qualified", "DBMS_CUSTOM.RUN", label="CALL", parent_id="routine:caller"),
            _node("call:bare", "SYS_TIME", label="CALL", parent_id="routine:caller"),
        ]

        edges = asyncio.run(CallLinker().link(nodes))

        self.assertEqual([edge.scope for edge in edges], ["external", "external"])
        self.assertEqual([edge.callee_id for edge in edges], ["RUN", "SYS_TIME"])


if __name__ == "__main__":
    unittest.main()
