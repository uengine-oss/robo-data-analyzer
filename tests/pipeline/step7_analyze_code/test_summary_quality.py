from __future__ import annotations

import unittest

from pydantic import ValidationError

from pipeline.models.code_analysis_output import (
    BatchSummaryItem,
    SummaryOutput,
)
from pipeline.steps.step7_analyze_code.summary_quality import normalize_searchable_text


class SummaryPreservationTest(unittest.TestCase):
    """최초 생성 summary의 의미 내용은 후처리에서 축약·절단하지 않는다."""

    @staticmethod
    def _summary(text: str) -> str:
        return SummaryOutput(search_text="검색 설명", summary=text, stereotype="").summary

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


class SearchableTextContractTest(unittest.TestCase):
    def test_missing_search_text_is_rejected_without_copying_summary(self):
        with self.assertRaisesRegex(ValueError, "search_text is empty"):
            normalize_searchable_text("Serializes and forwards the payload.", "")

    def test_missing_summary_is_rejected_without_copying_search_text(self):
        with self.assertRaisesRegex(ValueError, "summary is empty"):
            normalize_searchable_text("", "Déclare uniquement la signature.")

    def test_both_empty_are_rejected_instead_of_name_inference(self):
        with self.assertRaisesRegex(ValueError, "summary is empty"):
            normalize_searchable_text("", " ")

    def test_structured_models_reject_a_completely_empty_semantic_result(self):
        with self.assertRaises(ValidationError):
            SummaryOutput(summary="", search_text="")
        with self.assertRaises(ValidationError):
            BatchSummaryItem(node_key="1-1", summary="", search_text="")


if __name__ == "__main__":
    unittest.main()
