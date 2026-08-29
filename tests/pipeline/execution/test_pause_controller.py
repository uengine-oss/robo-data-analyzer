"""PauseController inflight cancellation and collection contracts."""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

from pipeline.execution.pause_controller import PauseController
from pipeline.execution.pause_controller import RunStopped
from llm.fanout import gather_with_warmup


class PauseControllerContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_fanout_failure_cancels_and_collects_siblings(self) -> None:
        sibling_started = asyncio.Event()
        sibling_cleaned = asyncio.Event()

        async def warmup() -> str:
            return "warm"

        async def fail() -> None:
            await sibling_started.wait()
            raise RuntimeError("semantic failure")

        async def sibling() -> None:
            try:
                sibling_started.set()
                await asyncio.Event().wait()
            finally:
                sibling_cleaned.set()

        with self.assertRaisesRegex(RuntimeError, "semantic failure"):
            await gather_with_warmup([warmup(), fail(), sibling()])
        self.assertTrue(sibling_cleaned.is_set())

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
            return await gather_with_warmup([blocking(), asyncio.Event().wait()])

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

    def test_both_step7_engines_use_the_shared_async_cleanup(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        for relative_path in (
            "pipeline/steps/step7_analyze_code/framework_per_node.py",
            "pipeline/steps/step7_analyze_code/batch_analyze.py",
        ):
            source = (repository_root / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                # 계약: 두 엔진 모두 공유 비동기 정리를 호출한다. 인자 표기는 엔진별
                # 자료구조에 따름 (framework dataflow 는 pending dict — spec 079).
                self.assertIn("await pause.cancel_and_wait(", source)

        framework_source = (
            repository_root / "pipeline/steps/step7_analyze_code/framework_per_node.py"
        ).read_text(encoding="utf-8")
        self.assertIn("await pause.gate()", framework_source)
        self.assertIn("pause.set_inflight(list(pending))", framework_source)

    def test_all_step7_item_fanouts_are_restartable_and_stop_responsive(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        for relative_path in (
            "pipeline/steps/step8_post_processing/enrich_table_description_step.py",
            "pipeline/steps/step8_post_processing/build_module_summary_rollups_step.py",
        ):
            source = (repository_root / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertIn("pause.run_restartable_during_pause", source)

        component_source = (
            repository_root
            / "pipeline/steps/step8_post_processing/build_components_step.py"
        ).read_text(encoding="utf-8")
        self.assertIn("await asyncio.to_thread(_cluster", component_source)
        self.assertGreaterEqual(component_source.count("await pause.gate()"), 2)
        self.assertNotIn("pause.run_restartable_during_pause", component_source)


if __name__ == "__main__":
    unittest.main()
