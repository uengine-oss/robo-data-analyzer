"""Workflow definition and RunnableConfig entry-boundary contracts."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from pipeline.execution.langgraph_builder import build_langgraph_workflow
from pipeline.execution.step_config import step_config


async def _first_step(state, config=None):
    return {}


async def _second_step(state, config=None):
    return {}


class WorkflowConfigurationContractTest(unittest.TestCase):
    def test_step_config_rejects_non_mapping_configurable(self) -> None:
        self.assertEqual({}, step_config(None))
        with self.assertRaisesRegex(TypeError, "configurable"):
            step_config({"configurable": "not-a-mapping"})  # type: ignore[arg-type]

    def test_builder_rejects_empty_duplicate_and_noncallable_steps(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one step"):
            build_langgraph_workflow(SimpleNamespace(steps=[]))
        with self.assertRaisesRegex(ValueError, "parallel group.*empty"):
            build_langgraph_workflow(SimpleNamespace(steps=[()]))
        with self.assertRaisesRegex(TypeError, "not callable"):
            build_langgraph_workflow(SimpleNamespace(steps=["step-name"]))

        duplicate = _second_step
        original_name = duplicate.__name__
        duplicate.__name__ = _first_step.__name__
        try:
            with self.assertRaisesRegex(ValueError, "duplicate step name"):
                build_langgraph_workflow(SimpleNamespace(steps=[_first_step, duplicate]))
        finally:
            duplicate.__name__ = original_name


if __name__ == "__main__":
    unittest.main()
