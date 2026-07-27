"""Generated semantic fields never fill one another when an LLM output is missing."""
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
from pipeline.steps.step7_analyze_code.range_body import compact_body
from pipeline.steps.step7_analyze_code.summary_quality import normalize_searchable_text
from pipeline.steps.step7_analyze_code.three_split_llm_fanout import (
    _format_callee_summaries,
)
from pipeline.steps.step8_post_processing.build_glossary_step import _union_groups
from pipeline.steps.step8_post_processing.embed_step import (
    _collect_column_texts,
    _collect_table_texts,
)
from shared.files.cache import SourceFileCache


ROOT = Path(__file__).resolve().parents[3]
GENERATED_FIELDS = {
    "summary",
    "search_text",
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


class RequiredIndependentOutputTest(unittest.TestCase):
    def test_summary_models_reject_each_missing_required_field(self) -> None:
        cases = (
            (SummaryOutput, {"summary": "사용자 요청을 검증한다.", "search_text": ""}),
            (SummaryOutput, {"summary": "", "search_text": "요청 검증 루틴"}),
            (
                BatchSummaryItem,
                {"node_key": "1-2", "summary": "레코드를 저장한다.", "search_text": ""},
            ),
            (
                BatchSummaryItem,
                {"node_key": "1-2", "summary": "", "search_text": "레코드 저장 루틴"},
            ),
        )
        for model, values in cases:
            with self.subTest(model=model.__name__, values=values):
                with self.assertRaises(ValidationError):
                    model(**values)

    def test_normalizer_strips_but_never_copies(self) -> None:
        self.assertEqual(
            ("사용자용 요약", "검색용 설명"),
            normalize_searchable_text(" 사용자용 요약 ", " 검색용 설명 "),
        )
        with self.assertRaisesRegex(ValueError, "search_text"):
            normalize_searchable_text("사용자용 요약", "")
        with self.assertRaisesRegex(ValueError, "summary"):
            normalize_searchable_text("", "검색용 설명")


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
                one_liner="조건을 검사한다",
                summary=None,
            )
            with self.assertRaisesRegex(ValueError, "without LLM summary"):
                compact_body(parent, {"parent": parent, "child": child}, SourceFileCache())

    def test_missing_callee_summary_is_not_replaced_by_one_liner(self) -> None:
        caller = AnalysisContext(
            id="caller", signature="caller", file_path="x", start_line=1, end_line=1,
            callees=("callee",),
        )
        callee = AnalysisContext(
            id="callee", signature="callee", file_path="x", start_line=1, end_line=1,
            one_liner="대체하면 안 되는 압축문", summary=None,
        )
        self.assertEqual("(없음)", _format_callee_summaries(caller, {"callee": callee}))

    def test_rejected_glossary_synthesis_does_not_copy_candidate_meaning(self) -> None:
        terms = {
            "record": {
                "canonical": "Record",
                "meanings": ["입력 단계에서 수집된 후보 뜻"],
            }
        }
        self.assertEqual([], _union_groups(terms, accepted={}))
        self.assertEqual(
            "근거가 통과한 LLM 종합 뜻",
            _union_groups(
                terms,
                accepted={
                    "record": {
                        "meaning": "근거가 통과한 LLM 종합 뜻",
                        "aliases": [],
                    }
                },
            )[0]["meaning"],
        )


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
                ("db:db.public.orders", "orders: DDL 원본 설명"),
                ("db:db.public.items", "items: LLM 대체 설명"),
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
                    r"(?m)^\s*(?:\w+\.)?(summary|search_text|logical_name|meaning)"
                    r"\s*=\s*(summary|search_text|logical_name|one_liner|meaning)\s*(?:#.*)?$"
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
