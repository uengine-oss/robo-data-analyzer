from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext, ControlSubtreeNode
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.steps.step2_load_ast.load_ast_step import _with_code_text
from pipeline.steps.step7_analyze_code.range_body import body_for, compact_body
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

    def test_framework_body_and_branch_anchor_share_absolute_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.c"
            lines = [""] * 99 + ["int run(void) {", "if (flag) {", "return 1;", "}", "}"]
            source.write_text("\n".join(lines), encoding="utf-8")
            ctx = AnalysisContext(
                id="fn",
                signature="run()",
                file_path=str(source),
                start_line=100,
                end_line=104,
            )

            rendered = body_for(ctx, {"fn": ctx}, SourceFileCache())
            inventory = build_inventory(
                "\n".join(lines[99:104]),
                [ControlSubtreeNode("if", "IF", 2, 4, expression="flag")],
                "c",
                statements=(),
                line_offset=99,
            )

            self.assertIn("100 | int run(void)", rendered)
            self.assertIn("101 | if (flag)", rendered)
            rows = inventory.branch_index().splitlines()
            # `int run(void) {` 는 선언 줄 — 효과문이 아니므로 [entry] 행이 생기지 않는다
            self.assertTrue(rows[0].startswith("101:"), rows)
            self.assertIsNotNone(inventory.at_line(101))

    def test_compaction_marks_child_summary_and_keeps_child_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.sql"
            source.write_text(
                "BEGIN\nIF flag THEN\nNULL;\nEND IF;\nEND;", encoding="utf-8"
            )
            parent = AnalysisContext(
                id="parent",
                signature="parent",
                file_path=str(source),
                start_line=1,
                end_line=5,
                extra={"child_ids": ("child",)},
            )
            child = AnalysisContext(
                id="child",
                signature="child",
                file_path=str(source),
                start_line=2,
                end_line=4,
                summary="조건이 참이면 처리를 수행한다.",
            )

            rendered = compact_body(
                parent, {"parent": parent, "child": child}, SourceFileCache()
            )

            self.assertIn(
                "2-4 | [CHILD_SUMMARY_CANDIDATE child_range=2-4 "
                "authority=non_source evidence_status=sufficient missing_context=[]]",
                rendered,
            )
            self.assertIn("1 | BEGIN", rendered)
            self.assertIn("5 | END;", rendered)

    def test_body_compaction_does_not_reuse_batch_packing_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.sql"
            source.write_text(
                "BEGIN\nIF value <> 'D' THEN\nflag := 'Y';\nEND IF;\nEND;",
                encoding="utf-8",
            )
            parent = AnalysisContext(
                id="parent",
                signature="parent",
                file_path=str(source),
                start_line=1,
                end_line=5,
                token_count=2_000,
                extra={"child_ids": ("child",)},
            )
            child = AnalysisContext(
                id="child",
                signature="child",
                file_path=str(source),
                start_line=2,
                end_line=4,
                summary="value가 D이면 flag를 Y로 설정한다.",
            )

            rendered = body_for(
                parent, {"parent": parent, "child": child}, SourceFileCache(),
            )

            self.assertIn("value <> 'D'", rendered)
            self.assertNotIn("CHILD_SUMMARY_CANDIDATE", rendered)


if __name__ == "__main__":
    unittest.main()
