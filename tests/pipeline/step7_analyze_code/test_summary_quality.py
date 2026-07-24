from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from pipeline.models.code_analysis_output import SummaryOutput, SummaryRewriteOutput
from pipeline.steps.step7_analyze_code.summary_quality import (
    enforce_summary_boundary,
    summary_boundary_reasons,
)


class SummaryDietNormalizeTest(unittest.TestCase):
    """``1~3줄`` 계약은 문장 수로도 걸려야 한다 — 모델은 줄바꿈 없이 쓴다."""

    @staticmethod
    def _summary(text: str) -> str:
        return SummaryOutput(search_text="", summary=text, stereotype="").summary

    def test_clips_to_three_sentences_on_a_single_line(self):
        got = self._summary("첫째 문장이다. 둘째 문장이다. 셋째 문장이다. 넷째 문장이다.")
        self.assertEqual("첫째 문장이다. 둘째 문장이다. 셋째 문장이다.", got)

    def test_keeps_three_sentences_untouched(self):
        text = "첫째 문장이다. 둘째 문장이다. 셋째 문장이다."
        self.assertEqual(text, self._summary(text))

    def test_decimal_and_version_dots_are_not_sentence_ends(self):
        text = "배송비 3,000.50원과 규격 v1.2 를 적용해 산정한다."
        self.assertEqual(text, self._summary(text))

    def test_sentence_clip_applies_after_line_clip(self):
        got = self._summary("한 줄이다. 또 한 문장이다.\n둘째 줄이다.\n셋째 줄.\n넷째 줄.")
        self.assertEqual("한 줄이다. 또 한 문장이다.\n둘째 줄이다.", got)


class SummaryQualityTest(unittest.IsolatedAsyncioTestCase):
    def test_detects_rule_markers_policy_numbers_and_multiple_lines(self):
        reasons = summary_boundary_reasons(
            "금액이 2000000 이상인 경우 재인증하고\n실패하면 -1을 반환한다.\n"
            "그 밖에는 통과시킨다.\n네 번째 줄."
        )
        self.assertEqual(
            ["rule_marker", "policy_number_or_code", "multiple_lines"], reasons,
        )

    def test_line_budget_matches_schema_so_plain_summaries_survive(self):
        """줄 수 상한은 스키마(1~3줄)와 같다 — 조건 없는 3줄 요약은 재작성 대상이 아니다.

        더 좁게 잡으면 정상 요약까지 재작성으로 끌려가 업무 내용이 깎인다(실측 근거).
        """
        self.assertEqual([], summary_boundary_reasons(
            "주문 정보를 바탕으로 배송비를 산정해 반환한다.\n"
            "산정 결과는 주문 금액에 반영된다.\n"
            "등급별 정책은 회원 정책 모듈에서 조회해 사용한다."
        ))

    def test_one_conditional_marker_is_enough_to_require_rewrite(self):
        self.assertEqual(["rule_marker"], summary_boundary_reasons(
            "재고가 안전 기준을 하회하면 경고 이벤트를 기록한다."
        ))

    def test_code_nouns_are_not_mistaken_for_rule_branches(self):
        self.assertEqual([], summary_boundary_reasons(
            "표준 반환코드와 상태를 조립하여 출력하고 전역 오류코드를 갱신한다."
        ))

    async def test_clean_purpose_summary_skips_llm(self):
        with patch(
            "pipeline.steps.step7_analyze_code.summary_quality.run_yaml_rule",
            new_callable=AsyncMock,
        ) as call:
            result = await enforce_summary_boundary(
                summary="결제 요청의 추가 인증 필요 여부를 판단한다.",
                node_name="need_pay_reauth", logical_name="결제 재인증 판단",
                search_text="", budget=None, audit_id="code:x:need_pay_reauth",
            )
        self.assertEqual("결제 요청의 추가 인증 필요 여부를 판단한다.", result)
        call.assert_not_awaited()

    async def test_invalid_draft_is_rewritten_and_revalidated(self):
        rewrite = SummaryRewriteOutput(summary="결제 요청의 추가 인증 필요 여부를 판단한다.")
        with patch(
            "pipeline.steps.step7_analyze_code.summary_quality.run_yaml_rule",
            new=AsyncMock(return_value=rewrite),
        ) as call:
            result = await enforce_summary_boundary(
                summary="금액이 2000000 이상인 경우 재인증 필요를 반환한다.",
                node_name="need_pay_reauth", logical_name="결제 재인증 판단",
                search_text="기준액 이상이면 재인증한다.", budget=None,
                audit_id="code:x:need_pay_reauth",
            )
        self.assertEqual(rewrite.summary, result)
        call.assert_awaited_once()

    async def test_rewrite_failure_preserves_original_summary(self):
        original = "오류인 경우 실패를 반환한다."
        with patch(
            "pipeline.steps.step7_analyze_code.summary_quality.run_yaml_rule",
            new=AsyncMock(side_effect=RuntimeError("provider down")),
        ):
            result = await enforce_summary_boundary(
                summary=original, node_name="x", logical_name="x", search_text="",
                budget=None, audit_id="code:x",
            )
        self.assertEqual(original, result)

    async def test_marker_only_draft_is_preserved_after_two_semantic_rewrites(self):
        reviewed = SummaryRewriteOutput(summary="회원의 일일 인증 한도 초과 여부를 판단한다.")
        with patch(
            "pipeline.steps.step7_analyze_code.summary_quality.run_yaml_rule",
            new=AsyncMock(return_value=reviewed),
        ) as call:
            result = await enforce_summary_boundary(
                summary="인증 횟수가 제한을 초과하면 거부한다.",
                node_name="daily_auth_over", logical_name="일일 인증 한도 판단",
                search_text="인증 횟수 제한을 검사한다.", budget=None,
                audit_id="code:x:daily_auth_over",
            )
        self.assertEqual(reviewed.summary, result)
        self.assertEqual(2, call.await_count)


if __name__ == "__main__":
    unittest.main()
