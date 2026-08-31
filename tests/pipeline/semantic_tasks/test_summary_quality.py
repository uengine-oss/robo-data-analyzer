from __future__ import annotations

import unittest

from pydantic import ValidationError

from graph.schema.graph_nodes import CodeNode
from pipeline.models.code_analysis_output import (
    BatchSummaryItem,
    SummaryOutput,
)
from pipeline.models.analysis_context import ReferencedSymbolContext
from pipeline.semantic_tasks.summary.quality import (
    copied_callee_summary_id,
    missing_structural_summary_identities,
    normalize_canonical_summary,
    unsupported_code_comparison_claims,
)


class SummaryPreservationTest(unittest.TestCase):
    """최초 생성 summary의 의미 내용은 후처리에서 축약·절단하지 않는다."""

    @staticmethod
    def _summary(text: str) -> str:
        return SummaryOutput(
            summary=text, stereotype="Helper", evidence_status="sufficient",
        ).summary

    def test_more_than_three_sentences_are_not_clipped(self):
        text = "첫째 문장이다. 둘째 문장이다. 셋째 문장이다. 넷째 문장이다."
        self.assertEqual(text, self._summary(text))

    def test_keeps_three_sentences_untouched(self):
        text = "첫째 문장이다. 둘째 문장이다. 셋째 문장이다."
        self.assertEqual(text, self._summary(text))

    def test_decimal_and_version_dots_are_not_sentence_ends(self):
        text = "배송비 3,000.50원과 규격 v1.2 를 적용해 산정한다."
        self.assertEqual(text, self._summary(text))

    def test_more_than_three_lines_are_not_clipped(self):
        text = "한 줄이다.\n둘째 줄이다.\n셋째 줄이다.\n넷째 줄이다."
        self.assertEqual(text, self._summary(text))

    def test_legacy_marker_cleanup_does_not_remove_content(self):
        self.assertEqual(
            "대상을 조회한다.\n결과를 반환한다.",
            self._summary("[핵심] 대상을 조회한다.\n결과를 반환한다."),
        )


class CanonicalSummaryContractTest(unittest.TestCase):
    def test_missing_summary_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "summary is empty"):
            normalize_canonical_summary("")

    def test_one_summary_is_trimmed_without_rewriting(self):
        self.assertEqual(
            "Déclare la responsabilité.",
            normalize_canonical_summary(" Déclare la responsabilité. "),
        )

    def test_exact_callee_copy_is_rejected_after_whitespace_normalization(self):
        self.assertEqual(
            "callee",
            copied_callee_summary_id(
                "  계좌번호\n정합성을  검증한다. ",
                [("callee", "계좌번호 정합성을 검증한다.")],
            ),
        )

    def test_distinct_owner_summary_is_not_rejected(self):
        self.assertIsNone(
            copied_callee_summary_id(
                "입력을 검증 계약으로 구성해 하위 처리에 위임한다.",
                [("callee", "계좌번호 정합성을 검증한다.")],
            )
        )

    def test_direct_ast_physical_identities_must_survive_summary(self):
        exit_node = CodeNode(
            id="exit:1", signature="EXIT", file_path="x.sql",
            start_line=1, end_line=1, label="EXIT",
            expression="C_SW_CALC%NOTFOUND",
        )
        call_node = CodeNode(
            id="call:2", signature="CALL", file_path="x.sql",
            start_line=2, end_line=2, label="CALL",
            name="DBMS_OUTPUT.PUT_LINE",
        )

        self.assertEqual(
            ("C_SW_CALC%NOTFOUND",),
            missing_structural_summary_identities(
                "C_SW_CALC cursor has no next row (%NOTFOUND).", exit_node,
            ),
        )
        self.assertEqual(
            (),
            missing_structural_summary_identities(
                "Exit when C_SW_CALC%NOTFOUND is true.", exit_node,
            ),
        )
        self.assertEqual(
            ("DBMS_OUTPUT.PUT_LINE",),
            missing_structural_summary_identities("Emit the error message.", call_node),
        )

    def test_validator_does_not_derive_meaning_from_literals_or_operators(self):
        node = CodeNode(
            id="return:1", signature="RETURN", file_path="x.c",
            start_line=1, end_line=1, label="RETURN", expression="0",
        )
        self.assertEqual((), missing_structural_summary_identities("Return zero.", node))

    def test_rejects_explicit_comparison_operand_substitution(self):
        symbols = (
            ReferencedSymbolContext(id="p", name="IN_TAGSN", kind="parameter"),
            ReferencedSymbolContext(id="v", name="TAG_CNT", kind="local"),
        )
        self.assertEqual(
            ("IN_TAGSN >= 1 (source: TAG_CNT >= 1)",),
            unsupported_code_comparison_claims(
                "태그가 존재하고(IN_TAGSN >= 1) 작업을 수행한다.",
                "IF TAG_CNT >= 1 THEN",
                symbols,
            ),
        )

    def test_allows_exact_complement_and_simple_alias_comparisons(self):
        symbols = (
            ReferencedSymbolContext(
                id="p", name="WORKDATE", kind="parameter",
            ),
            ReferencedSymbolContext(
                id="v", name="IN_LOG_TIME", kind="local",
                declared_value="WORKDATE",
                initialization_sources=("WORKDATE",),
            ),
            ReferencedSymbolContext(id="d", name="TODAY_DD", kind="local"),
            ReferencedSymbolContext(id="c", name="TAG_CNT", kind="local"),
        )
        evidence = "IF TAG_CNT >= 1 AND IN_LOG_TIME < TODAY_DD THEN"
        self.assertEqual(
            (),
            unsupported_code_comparison_claims(
                "TAG_CNT < 1이면 건너뛰고 WORKDATE < TODAY_DD이면 진행한다.",
                evidence,
                symbols,
            ),
        )

    def test_ignores_natural_language_and_low_confidence_unmatched_claims(self):
        symbols = (
            ReferencedSymbolContext(id="p", name="IN_TAGSN", kind="parameter"),
            ReferencedSymbolContext(id="v", name="TAG_CNT", kind="local"),
        )
        self.assertEqual(
            (),
            unsupported_code_comparison_claims(
                "IN_TAGSN에 해당하는 행이 있으면 처리한다.",
                "IF TAG_CNT >= 1 THEN",
                symbols,
            ),
        )
        self.assertEqual(
            (),
            unsupported_code_comparison_claims(
                "IN_TAGSN = 7이면 처리한다.",
                "IF TAG_CNT >= 1 THEN",
                symbols,
            ),
        )

    def test_structured_models_reject_a_completely_empty_semantic_result(self):
        with self.assertRaises(ValidationError):
            SummaryOutput(summary="", stereotype="", evidence_status="sufficient")
        with self.assertRaises(ValidationError):
            BatchSummaryItem(
                node_key="1-1", summary="", stereotype="", evidence_status="sufficient",
            )

    def test_structured_models_expose_evidence_sufficiency(self):
        self.assertEqual(
            "표시",
            SummaryOutput(
                summary="표시", stereotype="Helper", evidence_status="sufficient",
            ).summary,
        )
        self.assertEqual(
            "표시",
            BatchSummaryItem(
                node_key="1-1", summary="표시", stereotype="Helper",
                evidence_status="sufficient",
            ).summary,
        )
        insufficient = SummaryOutput(
            summary="", stereotype="", evidence_status="insufficient",
            missing_context=["callee 계약 원문"],
        )
        self.assertEqual("", insufficient.summary)
        with self.assertRaises(ValidationError):
            SummaryOutput(
                summary="추측", stereotype="Helper", evidence_status="insufficient",
                missing_context=["callee 계약 원문"],
            )


if __name__ == "__main__":
    unittest.main()
