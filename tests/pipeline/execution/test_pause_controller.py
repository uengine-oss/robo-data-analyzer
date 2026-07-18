"""PauseController inflight cancellation and collection contracts."""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from pipeline.execution.pause_controller import PauseController
from pipeline.execution.pause_controller import RunStopped
from llm.llm_fanout import gather_with_warmup


class PauseControllerContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_interrupts_warmup_fanout_and_collects_children(self) -> None:
        pause = PauseController()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def blocking() -> None:
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        async def operation() -> list:
            return await gather_with_warmup(
                [blocking(), asyncio.Event().wait()], return_exceptions=True,
            )

        runner = asyncio.create_task(pause.run_restartable_during_pause(operation))
        await started.wait()
        pause.request_stop()
        await asyncio.sleep(0.05)

        self.assertTrue(runner.done(), "stop must not wait for the remaining warm-up fan-out")
        with self.assertRaises(RunStopped):
            await runner
        self.assertTrue(cleaned.is_set())

    async def test_pause_cancellation_retries_after_resume_but_external_cancel_propagates(self) -> None:
        pause = PauseController()
        first_started = asyncio.Event()
        first_cleaned = asyncio.Event()
        attempts = 0

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                try:
                    first_started.set()
                    await asyncio.Event().wait()
                finally:
                    first_cleaned.set()
            return "completed"

        runner = asyncio.create_task(pause.run_restartable_during_pause(operation))
        await first_started.wait()
        pause.request_pause()
        await asyncio.sleep(0)
        self.assertFalse(runner.done())
        pause.resume()

        self.assertEqual("completed", await runner)
        self.assertTrue(first_cleaned.is_set())
        self.assertEqual(2, attempts)

        external_started = asyncio.Event()
        external_attempts = 0

        async def externally_cancelled() -> None:
            nonlocal external_attempts
            external_attempts += 1
            external_started.set()
            await asyncio.Event().wait()

        external_runner = asyncio.create_task(
            pause.run_restartable_during_pause(externally_cancelled)
        )
        await external_started.wait()
        external_runner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await external_runner
        self.assertEqual(1, external_attempts)

    async def test_cancel_and_wait_finishes_task_cleanup_and_collects_cancellation(self) -> None:
        pause = PauseController()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def worker() -> None:
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        task = asyncio.create_task(worker())
        await started.wait()
        pause.set_inflight([task])
        pause.request_pause()

        await pause.cancel_and_wait([task])

        self.assertTrue(cleaned.is_set())
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
        self.assertEqual([], pause._inflight)

    def test_both_step6_engines_use_the_shared_async_cleanup(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        for relative_path in (
            "pipeline/steps/step6_analyze_code/framework_per_node.py",
            "pipeline/steps/step6_analyze_code/batch_analyze.py",
        ):
            source = (repository_root / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertIn("await pause.cancel_and_wait(tasks)", source)

        framework_source = (
            repository_root / "pipeline/steps/step6_analyze_code/framework_per_node.py"
        ).read_text(encoding="utf-8")
        self.assertIn("pause.run_restartable_during_pause", framework_source)

    def test_all_step7_item_fanouts_are_restartable_and_stop_responsive(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        for relative_path in (
            "pipeline/steps/step7_post_processing/build_glossary_step.py",
            "pipeline/steps/step7_post_processing/enrich_table_description_step.py",
            "pipeline/steps/step7_post_processing/build_components_step.py",
            "pipeline/steps/step7_post_processing/summarize_module_step.py",
        ):
            source = (repository_root / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertIn("pause.run_restartable_during_pause", source)


if __name__ == "__main__":
    unittest.main()
