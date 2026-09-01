from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.pause_controller import PauseController, RunStopped
from pipeline.parsing.facts.node_builders import ParseResult
from pipeline.stages.ast import load_ast_step as subject
from pipeline.stages.ast.ast_dispatch import ast_and_source_roots
from pipeline.parsing.resolution.reference_links import link_references
from pipeline.parsing.adapters.import_binding import build_import_resolution_ledger
from pipeline.stages.ast.dbms_reader import _read_dbms


class LoadAstControlTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _c_result(ordinal: int) -> ParseResult:
        source_id = f"file-{ordinal}.c"
        build_evidence_id = f"{ordinal:064x}"
        trace_evidence_id = f"{ordinal + 100:064x}"
        return ParseResult(
            evidence_sources=[{
                "source_id": source_id,
                "decoded_text_sha256": str(ordinal) * 64,
                "decoded_text": "",
            }],
            preprocessing_contexts=[{
                "source_id": source_id,
                "status": "unresolved",
                "trust": "unresolved",
                "build": {
                    "emittedCommandOccurrenceIds": [],
                    "unresolvedEvidenceIds": [build_evidence_id],
                },
                "trace": {"evidenceIds": [trace_evidence_id]},
            }],
        )

    def test_explicit_corpus_root_owns_source_and_ast_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            corpus = Path(temp) / "corpus"
            project = corpus / "source" / "project"
            project.mkdir(parents=True)
            self.assertEqual(
                (corpus / "analysis" / "project", corpus / "source"),
                ast_and_source_roots(project, corpus),
            )

    def test_explicit_corpus_rejects_cross_corpus_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            corpus = root / "corpus"
            foreign = root / "foreign" / "source"
            foreign.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "corpus_root 밖"):
                ast_and_source_roots(foreign, corpus)

    async def test_import_without_canonical_fact_cannot_create_a_scope(self):
        ledger = build_import_resolution_ledger(
            [{
                "name": '"shopmall.h"', "file": "order.c",
                "import_id": "", "evidence_status": "unsupported",
            }],
        )

        self.assertEqual([], ledger)

    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        ast_dir = root / "analysis"
        source_root = root / "source"
        ast_dir.mkdir()
        source_root.mkdir()
        (ast_dir / "a.json").write_text("{}", encoding="utf-8")
        (ast_dir / "b.json").write_text("{}", encoding="utf-8")
        project = SimpleNamespace(root=source_root, corpus_root=root)
        closure = SimpleNamespace(artifacts=tuple(
            SimpleNamespace(
                ast_path=path,
                ast_sha256="",
                language="",
                ast_document={},
            )
            for path in sorted(ast_dir.glob("*.json"))
        ))
        return temporary, ast_dir, source_root, project, closure

    async def test_stop_after_first_file_prevents_second_file_read(self):
        temporary, ast_dir, source_root, project, closure = self._fixture()
        self.addCleanup(temporary.cleanup)
        pause = PauseController()
        reads = 0

        def read_one(*_args, **_kwargs):
            nonlocal reads
            reads += 1
            if reads == 1:
                pause.request_stop()
            return self._c_result(reads)

        config = {"configurable": {"codebase": project, "pause": pause}}
        with (
            patch.object(subject, "ast_and_source_roots", return_value=(ast_dir, source_root)),
            patch.object(subject, "verify_source_ast_closure", return_value=closure),
            patch.object(subject, "read_antlr_json", side_effect=read_one),
        ):
            with self.assertRaises(RunStopped):
                await subject.load_ast_step({}, config)
        self.assertEqual(reads, 1)

    async def test_pause_after_first_file_waits_then_resumes_second_file(self):
        temporary, ast_dir, source_root, project, closure = self._fixture()
        self.addCleanup(temporary.cleanup)
        pause = PauseController()
        first_read = asyncio.Event()
        reads = 0

        def read_one(*_args, **_kwargs):
            nonlocal reads
            reads += 1
            if reads == 1:
                pause.request_pause()
                first_read.set()
            return self._c_result(reads)

        config = {"configurable": {"codebase": project, "pause": pause}}
        with (
            patch.object(subject, "ast_and_source_roots", return_value=(ast_dir, source_root)),
            patch.object(subject, "verify_source_ast_closure", return_value=closure),
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
                [], [], checkpoint=stop_during_linking,
            )
        self.assertEqual(checkpoints, 2)

    async def test_dbms_declaration_roles_come_from_ontology(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = {
                "type": "FILE", "fileName": "pkg.sql", "filePath": "pkg.sql",
                "children": [
                    {"type": "PARAMETER", "name": "p_id", "startLine": 1,
                     "identifierReferenceVersion": 1},
                    {"type": "VARIABLE", "name": "v_id", "startLine": 2,
                     "identifierReferenceVersion": 1},
                    {"type": "PACKAGE_VARIABLE", "name": "g_id", "startLine": 3,
                     "identifierReferenceVersion": 1},
                    {"type": "CONSTANT_FIELD", "name": "MAX_ID", "startLine": 4,
                     "identifierReferenceVersion": 1},
                ],
            }
            result = _read_dbms(raw, root, root / "pkg.json")

        self.assertEqual(
            {node.label for node in result.variables},
            {"PARAMETER", "VARIABLE", "PACKAGE_VARIABLE"},
        )
        self.assertEqual([node.label for node in result.constants], ["CONSTANT_FIELD"])
        self.assertEqual(result.code_nodes, [])


if __name__ == "__main__":
    unittest.main()
