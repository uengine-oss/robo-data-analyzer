"""One canonical summary stays isolated from unrelated generated fields."""
from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from graph.schema.graph_edges import HasColumn
from graph.schema.graph_nodes import Column, Table
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import (
    BatchSummaryItem,
    SummaryOutput,
)
from pipeline.steps.step7_analyze_code.range_body import (
    ancestor_context,
    compact_body,
    model_visible_raw_body,
)
from pipeline.steps.step7_analyze_code.summary_quality import normalize_canonical_summary
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    _format_callee_summaries,
)
from pipeline.steps.step8_post_processing.embed_step import (
    _collect_column_texts,
    _collect_table_texts,
)
from shared.files.cache import SourceFileCache


ROOT = Path(__file__).resolve().parents[3]
GENERATED_FIELDS = {
    "summary",
    "logical_name",
    "meaning",
    "rules",
    "examples",
    "questions",
    "analyzed_description",
}


def _semantic_refs(node: ast.AST) -> set[str]:
    refs: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in GENERATED_FIELDS:
            refs.add(child.attr)
        elif (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "get"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and child.args[0].value in GENERATED_FIELDS
        ):
            refs.add(child.args[0].value)
    return refs


def _target_field(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and node.id in GENERATED_FIELDS:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in GENERATED_FIELDS:
        return node.attr
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in GENERATED_FIELDS
    ):
        return node.slice.value
    return None


class RequiredMeaningOutputTest(unittest.TestCase):
    def test_summary_models_reject_missing_meaning(self) -> None:
        cases = (
            (SummaryOutput, {"summary": ""}),
            (
                BatchSummaryItem,
                {"node_key": "1-2", "summary": ""},
            ),
        )
        for model, values in cases:
            with self.subTest(model=model.__name__, values=values):
                with self.assertRaises(ValidationError):
                    model(**values)

    def test_normalizer_strips_and_preserves_canonical_summary(self) -> None:
        self.assertEqual("사용자 표시", normalize_canonical_summary(" 사용자 표시 "))
        with self.assertRaisesRegex(ValueError, "summary"):
            normalize_canonical_summary("")


class DownstreamEvidenceIndependenceTest(unittest.TestCase):
    def test_compaction_requires_the_child_llm_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.sql"
            source.write_text("BEGIN\nIF flag THEN\nNULL;\nEND IF;\nEND;\n", encoding="utf-8")
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
                summary=None,
            )
            with self.assertRaisesRegex(ValueError, "without LLM summary"):
                compact_body(parent, {"parent": parent, "child": child}, SourceFileCache())

    def test_rejected_child_keeps_exact_source_in_compaction(self) -> None:
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
            child = AnalysisContext(
                id="child", signature="child", file_path=str(source),
                start_line=2, end_line=2, summary=None,
                extra={"summary_status": "rejected"},
            )

            rendered = compact_body(
                parent, {"parent": parent, "child": child}, SourceFileCache(),
            )

            self.assertIn("2 | CALL child_proc();", rendered)
            self.assertNotIn("CONTEXT_SUMMARY", rendered)

    def test_missing_callee_summary_is_omitted(self) -> None:
        caller = AnalysisContext(
            id="caller", signature="caller", file_path="x", start_line=1, end_line=1,
            callees=("callee",),
        )
        callee = AnalysisContext(
            id="callee", signature="callee", file_path="x", start_line=1, end_line=1,
            summary=None,
        )
        self.assertEqual("(없음)", _format_callee_summaries(caller, {"callee": callee}))

    def test_ancestor_context_preserves_structural_skeleton(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.sql"
            source.write_text(
                "BEGIN\nIF account_is_locked THEN NULL; END IF;\nEND;\n",
                encoding="utf-8",
            )
            parent = AnalysisContext(
                id="parent",
                signature="parent",
                file_path=str(source),
                start_line=1,
                end_line=3,
                extra={"child_ids": ("child",)},
            )
            child = AnalysisContext(
                id="child",
                signature="child",
                file_path=str(source),
                start_line=2,
                end_line=2,
                extra={"parent_id": "parent"},
            )

            rendered = ancestor_context(
                child,
                {"parent": parent, "child": child},
                SourceFileCache(),
            )

            self.assertIn("BEGIN", rendered)
            self.assertIn("OMITTED_CHILD child_range=2-2", rendered)
            self.assertIn("END;", rendered)

    def test_model_visible_raw_body_blanks_compacted_child_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.sql"
            source.write_text(
                "BEGIN\nCHILD_SECRET\nchild_work;\nEND CHILD\nEND;\n",
                encoding="utf-8",
            )
            parent = AnalysisContext(
                id="parent",
                signature="parent",
                file_path=str(source),
                start_line=1,
                end_line=5,
                token_count=10**9,
                extra={"child_ids": ("child",)},
            )
            child = AnalysisContext(
                id="child",
                signature="child",
                file_path=str(source),
                start_line=2,
                end_line=4,
                summary="child summary",
            )

            visible = model_visible_raw_body(
                parent,
                {"parent": parent, "child": child},
                SourceFileCache(),
            )
            lines = visible.splitlines()

            self.assertEqual(5, len(lines))
            self.assertEqual("BEGIN", lines[0])
            self.assertEqual(["", "", ""], lines[1:4])
            self.assertEqual("END;", lines[4])
            self.assertNotIn("CHILD_SECRET", visible)

    def test_callee_handoff_uses_canonical_summary(self) -> None:
        caller = AnalysisContext(
            id="caller", signature="caller", file_path="x", start_line=1, end_line=1,
            callees=("callee",),
        )
        callee = AnalysisContext(
            id="callee", signature="callee", file_path="x", start_line=1, end_line=1,
            summary="상태 코드와 테이블을 포함한 canonical 요약",
        )
        rendered = _format_callee_summaries(caller, {"callee": callee})
        self.assertIn("상태 코드와 테이블을 포함한 canonical 요약", rendered)
        self.assertIn('"summary":', rendered)

    def test_callee_return_expressions_do_not_repeat_in_prompt(self) -> None:
        caller = AnalysisContext(
            id="caller", signature="caller", file_path="x", start_line=1, end_line=1,
            callees=("callee",),
        )
        callee = AnalysisContext(
            id="callee", signature="callee", file_path="x", start_line=1, end_line=1,
            summary="검색 근거",
            extra={"return_expressions": ["RC_ERR", "RC_NRM"]},
        )
        rendered = _format_callee_summaries(caller, {"callee": callee})
        self.assertNotIn("returns", rendered)
        self.assertNotIn("RC_ERR", rendered)

class SourceDescriptionExceptionTest(unittest.TestCase):
    def test_table_source_description_precedes_analyzed_description(self) -> None:
        tables = [
            Table(
                db="db", schema="public", name="orders",
                description="DDL 원본 설명", analyzed_description="LLM 분석 설명",
            ),
            Table(
                db="db", schema="public", name="items",
                description=" ", analyzed_description="LLM 대체 설명",
            ),
            Table(db="db", schema="public", name="empty"),
        ]
        self.assertEqual(
            [
                (tables[0].id, "orders: DDL 원본 설명"),
                (tables[1].id, "items: LLM 대체 설명"),
            ],
            _collect_table_texts(tables),
        )

    def test_column_uses_only_the_explicit_description_exception(self) -> None:
        table = Table(db="db", schema="public", name="orders")
        columns = [
            Column(
                id="col:source", name="source", dtype="TEXT",
                description="원본 설명", analyzed_description="분석 설명",
            ),
            Column(
                id="col:analyzed", name="analyzed", dtype="TEXT",
                analyzed_description="분석 설명",
            ),
            Column(id="col:empty", name="empty", dtype="TEXT", comment="암묵 대체 금지"),
        ]
        edges = [HasColumn(table.id, column.id) for column in columns]
        self.assertEqual(
            [
                ("col:source", "orders.source: 원본 설명"),
                ("col:analyzed", "orders.analyzed: 분석 설명"),
            ],
            _collect_column_texts(columns, [table], edges),
        )


class StaticSemanticCopyAuditTest(unittest.TestCase):
    def test_semantic_review_exporter_uses_only_canonical_summary(self) -> None:
        source = (
            ROOT / "scripts" / "export_semantic_review_packets.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("search_text", source)

    def test_step7_has_no_secondary_semantic_rewrite(self) -> None:
        step7 = ROOT / "pipeline" / "steps" / "step7_analyze_code"
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in step7.rglob("*.py")
        )
        self.assertNotIn("summary_rewrite", source)
        self.assertNotIn("enforce_summary_boundary", source)
        self.assertNotIn("run_rules_fallback", source)
        self.assertNotIn("deterministic_dedupe", source)

    def test_no_corrective_llm_rule_is_called(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for top in ("pipeline", "llm")
            for path in (ROOT / top).rglob("*.py")
        )
        corrective_rule = re.compile(
            r"""rule_name\s*=\s*["'][^"']*(?:rewrite|repair|review|judge|merge|fallback)[^"']*["']""",
            re.IGNORECASE,
        )
        self.assertIsNone(corrective_rule.search(source))

    def test_summary_formatting_never_clips_generated_content(self) -> None:
        source = (
            ROOT / "pipeline" / "models" / "code_analysis_output.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("_clip_sentences", source)
        self.assertNotRegex(source, r"lines\s*\[\s*:")

    def test_assignments_do_not_fill_generated_fields_from_siblings(self) -> None:
        violations: list[str] = []
        for top in ("pipeline", "llm", "search", "graph", "shared"):
            for path in (ROOT / top).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    pairs: list[tuple[str, ast.AST]] = []
                    if isinstance(node, ast.Assign):
                        pairs.extend(
                            (field, node.value)
                            for target in node.targets
                            if (field := _target_field(target))
                        )
                    elif isinstance(node, ast.AnnAssign):
                        field = _target_field(node.target)
                        if field and node.value is not None:
                            pairs.append((field, node.value))
                    elif isinstance(node, ast.Call):
                        pairs.extend(
                            (keyword.arg, keyword.value)
                            for keyword in node.keywords
                            if keyword.arg in GENERATED_FIELDS
                        )
                    for target, value in pairs:
                        # A call result may be a newly generated field; its named inputs are
                        # audited individually as call keywords below, not as copied output.
                        if isinstance(value, (ast.Call, ast.Await)):
                            continue
                        siblings = _semantic_refs(value) - {target}
                        # The only explicit cross-field exception: source description and
                        # independently generated analyzed_description. This audit concerns
                        # generated targets, so neither direction is generally permitted.
                        if siblings:
                            violations.append(
                                f"{path.relative_to(ROOT)}:{node.lineno}: "
                                f"{target} <- {sorted(siblings)}"
                            )
                text = path.read_text(encoding="utf-8")
                direct_copy = re.compile(
                    r"(?m)^\s*(?:\w+\.)?(summary|logical_name|meaning)"
                    r"\s*=\s*(summary|logical_name|meaning)\s*(?:#.*)?$"
                )
                for match in direct_copy.finditer(text):
                    if match.group(1) != match.group(2):
                        violations.append(
                            f"{path.relative_to(ROOT)}: direct {match.group(1)} "
                            f"<- {match.group(2)}"
                        )
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
