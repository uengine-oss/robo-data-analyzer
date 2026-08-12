from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from graph.schema.graph_nodes import Module
from pipeline.steps.step7_analyze_code.module_pre_analysis import run_module_pre_analysis
from shared.concurrency.priority_semaphore import PrioritySemaphore
from tests.pipeline.semantic_frame_fixtures import frame_wire_for_response_model


class ModulePreAnalysisSlotTest(unittest.IsolatedAsyncioTestCase):
    async def test_uses_the_supplied_run_scoped_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts = Path(temporary_directory)
            (prompts / "module_pre_analysis.yaml").write_text(
                "system: test\nuser_template: test\n", encoding="utf-8",
            )
            module = Module(
                id="code:x.c", name="x", file_path="x.c", label="FILE",
                comment="module purpose",
            )
            slots = PrioritySemaphore(1)
            await slots.acquire()
            async def _invoke(**kwargs):
                return frame_wire_for_response_model(
                    kwargs["response_model"], meaning="purpose",
                )
            invoke = AsyncMock(side_effect=_invoke)
            with patch(
                "pipeline.steps.step7_analyze_code.module_pre_analysis.run_yaml_rule",
                new=invoke,
            ):
                task = asyncio.create_task(run_module_pre_analysis(
                    [module], [], SimpleNamespace(prompts_dir=prompts), None, slots,
                ))
                await asyncio.sleep(0)
                self.assertEqual(0, invoke.await_count)
                self.assertEqual(1, slots.waiting)
                slots.release()
                self.assertEqual({"code:x.c": "purpose"}, await task)

        self.assertEqual(1, slots.peak_in_use)
        self.assertEqual(0, slots.in_use)


if __name__ == "__main__":
    unittest.main()
