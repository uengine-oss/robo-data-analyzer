from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.steps.step2_load_ast.load_ast_step import _with_code_text
from pipeline.semantic_tasks.source_body import exact_source_body
from shared.files.cache import SourceFileCache
from shared.files.source_excerpt import SourceExcerpt


class SourceExcerptContractTest(unittest.TestCase):
    def test_preserves_raw_text_and_renders_absolute_lines(self) -> None:
        excerpt = SourceExcerpt.from_lines(
            "sample.py", ["ignored", "def run():", "", "    return 1"], 2, 4
        )

        self.assertEqual("def run():\n\n    return 1", excerpt.raw_text)
        self.assertEqual(
            "2 | def run():\n3 | \n4 |     return 1",
            excerpt.numbered_text,
        )
        self.assertEqual("absolute-file-line", excerpt.coordinate_system)
        self.assertEqual("available", excerpt.status)

    def test_step2_attaches_numbered_public_and_exact_internal_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.c"
            source.write_text("zero\none\ntwo\n", encoding="utf-8")
            node = CodeNode(
                id="fn",
                signature="fn()",
                file_path=str(source),
                start_line=2,
                end_line=3,
                label="FUNCTION",
            )

            loaded = _with_code_text([node], SourceFileCache())[0]

            self.assertEqual("one\ntwo", loaded.raw_code_text)
            self.assertEqual("2 | one\n3 | two", loaded.code_text)

    def test_analysis_source_never_replaces_child_lines_with_generated_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.sql"
            source.write_text(
                "BEGIN\nCALL child_proc();\nEND;\n", encoding="utf-8",
            )
            parent = AnalysisContext(
                id="parent", signature="parent", file_path=str(source),
                start_line=1, end_line=3,
                extra={"child_ids": ("child",)},
            )

            rendered = exact_source_body(parent, SourceFileCache())

            self.assertEqual("BEGIN\nCALL child_proc();\nEND;", rendered)
            self.assertNotIn("SUMMARY", rendered)


if __name__ == "__main__":
    unittest.main()
