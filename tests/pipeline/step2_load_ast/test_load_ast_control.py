from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.pause_controller import PauseController, RunStopped
from pipeline.steps.step2_load_ast.node_builders import ParseResult
from pipeline.steps.step2_load_ast import load_ast_step as subject
from pipeline.steps.step2_load_ast.token_matcher import link_references
from pipeline.steps.step2_load_ast.import_scopes import _build_scopes


class LoadAstControlTest(unittest.IsolatedAsyncioTestCase):
    async def test_split_import_scope_retains_language_resolution(self):
        scopes = _build_scopes(
            [{"name": '"shopmall.h"', "file": "order.c"}],
            {},
            {},
            {},
        )

        self.assertEqual(scopes["order.c"].lang, "c")

    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        ast_dir = root / "analysis"
        source_root = root / "source"
        ast_dir.mkdir()
        source_root.mkdir()
        (ast_dir / "a.json").write_text("{}", encoding="utf-8")
        (ast_dir / "b.json").write_text("{}", encoding="utf-8")
        project = SimpleNamespace(root=source_root)
        return temporary, ast_dir, source_root, project

    async def test_stop_after_first_file_prevents_second_file_read(self):
        temporary, ast_dir, source_root, project = self._fixture()
        self.addCleanup(temporary.cleanup)
        pause = PauseController()
        reads = 0

        def read_one(*_args):
            nonlocal reads
            reads += 1
            if reads == 1:
                pause.request_stop()
            return ParseResult()

        config = {"configurable": {"codebase": project, "pause": pause}}
        with (
            patch.object(subject, "ast_and_source_roots", return_value=(ast_dir, source_root)),
            patch.object(subject, "read_antlr_json", side_effect=read_one),
        ):
            with self.assertRaises(RunStopped):
                await subject.load_ast_step({}, config)
        self.assertEqual(reads, 1)

    async def test_pause_after_first_file_waits_then_resumes_second_file(self):
        temporary, ast_dir, source_root, project = self._fixture()
        self.addCleanup(temporary.cleanup)
        pause = PauseController()
        first_read = asyncio.Event()
        reads = 0

        def read_one(*_args):
            nonlocal reads
            reads += 1
            if reads == 1:
                pause.request_pause()
                first_read.set()
            return ParseResult()

        config = {"configurable": {"codebase": project, "pause": pause}}
        with (
            patch.object(subject, "ast_and_source_roots", return_value=(ast_dir, source_root)),
            patch.object(subject, "read_antlr_json", side_effect=read_one),
        ):
            runner = asyncio.create_task(subject.load_ast_step({}, config))
            await first_read.wait()
            await asyncio.sleep(0)
            self.assertFalse(runner.done())
            self.assertEqual(reads, 1)
            pause.resume()
            await runner
        self.assertEqual(reads, 2)

    async def test_reference_linking_observes_checkpoint_stop(self):
        pause = PauseController()
        checkpoints = 0

        async def stop_during_linking():
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                pause.request_stop()
            await pause.gate()

        with self.assertRaises(RunStopped):
            await link_references(
                [CodeNode(
                    id="code:fixture/a.c:f", signature="void f(void)",
                    file_path="a.c", start_line=1, end_line=1,
                    label="FUNCTION", name="f", owner_id="code:fixture/a.c",
                )],
                [], [], [], checkpoint=stop_during_linking,
            )
        self.assertEqual(checkpoints, 2)


if __name__ == "__main__":
    unittest.main()
