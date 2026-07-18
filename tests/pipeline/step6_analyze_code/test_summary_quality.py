from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from pipeline.models.code_analysis_output import SummaryRewriteOutput
from pipeline.steps.step6_analyze_code.summary_quality import (
    enforce_summary_boundary,
    summary_boundary_reasons,
)


class SummaryQualityTest(unittest.IsolatedAsyncioTestCase):
    def test_detects_rule_markers_policy_numbers_and_multiple_lines(self):
        reasons = summary_boundary_reasons(
            "금액이 2000000 이상인 경우 재인증하고\n실패하면 -1을 반환한다."
        )
        self.assertEqual(
            ["rule_marker", "policy_number_or_code", "multiple_lines"], reasons,
        )

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
            "pipeline.steps.step6_analyze_code.summary_quality.run_yaml_rule",
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
            "pipeline.steps.step6_analyze_code.summary_quality.run_yaml_rule",
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
            "pipeline.steps.step6_analyze_code.summary_quality.run_yaml_rule",
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
            "pipeline.steps.step6_analyze_code.summary_quality.run_yaml_rule",
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
