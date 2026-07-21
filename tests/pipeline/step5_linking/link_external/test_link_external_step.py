from __future__ import annotations

import unittest

from pipeline.steps.step5_linking.link_external.link_external_step import link_external_step
from pipeline.strategies import DBMS_PIPELINE, FRAMEWORK_PIPELINE


class LinkExternalStepTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_upstream_state_is_explicit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "step2 and step3"):
            await link_external_step({}, {"configurable": {}})

    async def test_empty_completed_inputs_are_valid(self) -> None:
        result = await link_external_step(
            {"code_nodes": [], "has_member_edges": [], "external_chunks": []},
            {"configurable": {}},
        )
        self.assertEqual({"external_edges": [], "reads_writes_edges": [], "unresolved_refs": []}, result)

    def test_link_step_precedes_context_in_both_strategies(self) -> None:
        for strategy in (FRAMEWORK_PIPELINE, DBMS_PIPELINE):
            names = [
                group.__name__ if callable(group) else tuple(step.__name__ for step in group)
                for group in strategy.steps
            ]
            self.assertLess(names.index("link_external_step"), names.index("build_context_step"))


if __name__ == "__main__":
    unittest.main()
