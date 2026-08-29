from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pipeline.steps.step2_load_ast.load_ast_step import load_ast_step
from pipeline.steps.step2_load_ast.source_ast_closure import verify_source_ast_closure
from shared.files.cache import SourceFileCache


def _ast(source_name: str) -> dict:
    return {
        "type": "FILE",
        "fileName": Path(source_name).name,
        "filePath": source_name,
        "startLine": 1,
        "endLine": 1,
        "children": [],
    }


def _diagnostic(source_name: str, payload: bytes, *, status: str = "EXACT") -> dict:
    return {
        "schemaVersion": "1.1.0",
        "sourcePath": source_name,
        "sourceSha256": hashlib.sha256(payload).hexdigest(),
        "language": "c",
        "status": status,
        "firstPass": {
            "antlrRecoveries": 0,
            "coverage": {
                "knownAndComplete": status == "EXACT",
                "missingDeclarations": [],
            },
        },
        "summary": {"lexerErrors": 0, "parserErrors": 0},
    }


class SourceAstClosureTest(unittest.IsolatedAsyncioTestCase):
    def _corpus(self):
        temporary = tempfile.TemporaryDirectory()
        corpus = Path(temporary.name)
        source = corpus / "source"
        analysis = corpus / "analysis"
        diagnostics = corpus / "diagnostics"
        source.mkdir()
        analysis.mkdir()
        diagnostics.mkdir()
        project = SimpleNamespace(root=source, corpus_root=corpus)
        return temporary, source, analysis, diagnostics, project

    async def test_supported_source_without_ast_and_diagnostic_fails_closed(self) -> None:
        temporary, source, _analysis, _diagnostics, project = self._corpus()
        self.addCleanup(temporary.cleanup)
        (source / "missing.c").write_text("int f(void) { return 0; }\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "missing.c"):
            await load_ast_step({}, {"configurable": {"codebase": project}})

    async def test_stale_diagnostic_source_hash_fails_before_ast_materialization(self) -> None:
        temporary, source, analysis, diagnostics, project = self._corpus()
        self.addCleanup(temporary.cleanup)
        payload = b"int f(void) { return 0; }\n"
        (source / "sample.c").write_bytes(payload)
        (analysis / "sample.json").write_text(json.dumps(_ast("sample.c")), encoding="utf-8")
        stale = _diagnostic("sample.c", b"different source")
        (diagnostics / "sample.c.parse.json").write_text(json.dumps(stale), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "sourceSha256|hash"):
            await load_ast_step({}, {"configurable": {"codebase": project}})

    async def test_non_exact_parser_diagnostic_fails_closed(self) -> None:
        temporary, source, analysis, diagnostics, project = self._corpus()
        self.addCleanup(temporary.cleanup)
        payload = b"int f(void) { return 0; }\n"
        (source / "sample.c").write_bytes(payload)
        (analysis / "sample.json").write_text(json.dumps(_ast("sample.c")), encoding="utf-8")
        degraded = _diagnostic("sample.c", payload, status="RECOVERED")
        (diagnostics / "sample.c.parse.json").write_text(json.dumps(degraded), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "EXACT|RECOVERED"):
            await load_ast_step({}, {"configurable": {"codebase": project}})

    def test_valid_population_is_accounted_exactly_once_with_all_hashes(self) -> None:
        temporary, source, analysis, diagnostics, _project = self._corpus()
        self.addCleanup(temporary.cleanup)
        payload = b"int f(void) { return 0; }\n"
        (source / "sample.c").write_bytes(payload)
        (analysis / "sample.json").write_text(json.dumps(_ast("sample.c")), encoding="utf-8")
        (diagnostics / "sample.c.parse.json").write_text(
            json.dumps(_diagnostic("sample.c", payload)), encoding="utf-8"
        )

        closure = verify_source_ast_closure(
            project_root=source,
            ast_root=analysis,
            source_root=source,
            source_cache=SourceFileCache(),
        )

        self.assertEqual(("sample.c",), tuple(item.source_id for item in closure.artifacts))
        self.assertEqual(_ast("sample.c"), closure.artifacts[0].ast_document)
        manifest = closure.as_dict()
        self.assertEqual(
            (1, 1, 1),
            (
                manifest["expected_sources"],
                manifest["ast_artifacts"],
                manifest["diagnostics"],
            ),
        )
        self.assertTrue(all(len(manifest["artifacts"][0][key]) == 64 for key in (
            "source_sha256", "ast_sha256", "diagnostic_sha256",
        )))

    def test_duplicate_ast_for_one_source_fails_exact_once_contract(self) -> None:
        temporary, source, analysis, diagnostics, _project = self._corpus()
        self.addCleanup(temporary.cleanup)
        payload = b"int f(void) { return 0; }\n"
        (source / "sample.c").write_bytes(payload)
        ast = json.dumps(_ast("sample.c"))
        (analysis / "one.json").write_text(ast, encoding="utf-8")
        (analysis / "two.json").write_text(ast, encoding="utf-8")
        (diagnostics / "sample.c.parse.json").write_text(
            json.dumps(_diagnostic("sample.c", payload)), encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "duplicate AST"):
            verify_source_ast_closure(
                project_root=source,
                ast_root=analysis,
                source_root=source,
                source_cache=SourceFileCache(),
            )

    def test_explicit_partial_upload_closes_only_selected_source_ids(self) -> None:
        temporary, source, analysis, diagnostics, _project = self._corpus()
        self.addCleanup(temporary.cleanup)
        for name in ("selected.c", "outside.c"):
            payload = f"int {Path(name).stem}(void) {{ return 0; }}\n".encode()
            (source / name).write_bytes(payload)
            (analysis / f"{Path(name).stem}.json").write_text(
                json.dumps(_ast(name)), encoding="utf-8",
            )
            (diagnostics / f"{name}.parse.json").write_text(
                json.dumps(_diagnostic(name, payload)), encoding="utf-8",
            )

        closure = verify_source_ast_closure(
            project_root=source,
            ast_root=analysis,
            source_root=source,
            source_cache=SourceFileCache(),
            selected_source_ids=("selected.c",),
        )

        self.assertEqual(
            ("selected.c",),
            tuple(item.source_id for item in closure.artifacts),
        )
        self.assertEqual(1, closure.as_dict()["expected_sources"])

    def test_missing_selected_source_fails_instead_of_falling_back_to_all(self) -> None:
        temporary, source, analysis, _diagnostics, _project = self._corpus()
        self.addCleanup(temporary.cleanup)
        (source / "outside.c").write_text("int outside;\n", encoding="utf-8")
        (analysis / "outside.json").write_text(
            json.dumps(_ast("outside.c")), encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "selected source file not found"):
            verify_source_ast_closure(
                project_root=source,
                ast_root=analysis,
                source_root=source,
                source_cache=SourceFileCache(),
                selected_source_ids=("missing.c",),
            )

    def test_invalid_utf8_source_is_not_silently_shortened(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "invalid.c"
            source.write_bytes(b"before\xffafter")

            with self.assertRaises(UnicodeDecodeError):
                SourceFileCache().lines(str(source))


if __name__ == "__main__":
    unittest.main()
