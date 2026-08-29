from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step8_post_processing.build_components_step import (
    build_components_step,
)


def _routine(stable_id: str) -> CodeNode:
    return CodeNode(
        id=stable_id,
        signature=f"{stable_id}(void)",
        file_path="orders.c",
        start_line=1,
        end_line=10,
        label="FUNCTION",
        name=stable_id,
        owner_id="module:orders.c",
    )


class ComponentProductProjectionStepTest(unittest.IsolatedAsyncioTestCase):
    async def test_step_returns_membership_without_graph_write_or_summary_wait(self) -> None:
        neo = AsyncMock()
        expected = [("COMPONENT:code:a", ["code:a", "code:b"])]
        with patch(
            "pipeline.steps.step8_post_processing.build_components_step._cluster",
            return_value=expected,
        ):
            result = await build_components_step(
                {"code_nodes": [_routine("code:a"), _routine("code:b")]},
                {"configurable": {"neo_client": neo}},
            )

        self.assertEqual(
            {"COMPONENT:code:a": ["code:a", "code:b"]},
            result["component_member_ids_by_component_id"],
        )
        neo.run_batch_unwind.assert_not_awaited()
        neo.run_query.assert_not_awaited()

    async def test_empty_population_is_explicit(self) -> None:
        result = await build_components_step({}, {"configurable": {}})
        self.assertEqual(
            {"component_member_ids_by_component_id": {}},
            result,
        )


if __name__ == "__main__":
    unittest.main()
