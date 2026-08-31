"""One canonical summary stays isolated from unrelated generated fields."""
from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import (
    BatchSummaryItem,
    SummaryOutput,
)
from pipeline.semantic_tasks.summary.quality import normalize_canonical_summary
from pipeline.semantic_tasks.three_split_llm_fanout import (
    _direct_callee_outcomes_by_id,
)
from shared.files.cache import SourceFileCache


ROOT = Path(__file__).resolve().parents[3]
GENERATED_FIELDS = {
    "summary",
    "logical_name",
    "meaning",
    "rules",
    "examples",
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
    def test_missing_callee_summary_is_omitted(self) -> None:
        caller = AnalysisContext(
            id="caller", signature="caller", file_path="x", start_line=1, end_line=1,
            callees=("callee",),
        )
        callee = AnalysisContext(
            id="callee", signature="callee", file_path="x", start_line=1, end_line=1,
            summary=None,
        )
        self.assertEqual({}, _direct_callee_outcomes_by_id(caller, {"callee": callee}))

    def test_callee_handoff_uses_canonical_summary(self) -> None:
        caller = AnalysisContext(
            id="caller", signature="caller", file_path="x", start_line=1, end_line=1,
            callees=("callee",),
        )
        callee = AnalysisContext(
            id="callee", signature="callee", file_path="x", start_line=1, end_line=1,
            summary="상태 코드와 테이블을 포함한 canonical 요약",
        )
        rendered = _direct_callee_outcomes_by_id(caller, {"callee": callee})
        self.assertEqual({
            "callee": {
                "callee": "callee",
                "meaning": "상태 코드와 테이블을 포함한 canonical 요약",
            },
        }, rendered)

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
        rendered = str(_direct_callee_outcomes_by_id(caller, {"callee": callee}))
        self.assertNotIn("returns", rendered)
        self.assertNotIn("RC_ERR", rendered)

class StaticSemanticCopyAuditTest(unittest.TestCase):
    def test_semantic_review_exporter_uses_only_canonical_summary(self) -> None:
        source = (
            ROOT / "tools" / "verify" / "export_semantic_review_packets.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("search_text", source)

    def test_step7_has_no_secondary_semantic_rewrite(self) -> None:
        step7 = ROOT / "pipeline" / "semantic_tasks"
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
