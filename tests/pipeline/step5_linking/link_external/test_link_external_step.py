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
        self.assertEqual({"external_edges": []}, result)

    def test_link_step_precedes_context_in_both_strategies(self) -> None:
        # 그룹(튜플) 구성원은 그룹 위치를 공유한다 — 순서 비교는 그룹 인덱스 기준.
        for strategy in (FRAMEWORK_PIPELINE, DBMS_PIPELINE):
            def _position(step_name: str) -> int:
                for idx, group in enumerate(strategy.steps):
                    members = (group,) if callable(group) else group
                    if any(step.__name__ == step_name for step in members):
                        return idx
                raise AssertionError(f"{step_name} not in {strategy.name} steps")
            self.assertLess(_position("link_external_step"), _position("build_context_step"))


if __name__ == "__main__":
    unittest.main()
