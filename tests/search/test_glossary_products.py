from __future__ import annotations

import unittest
from unittest.mock import patch

from search.query.glossary import render_for_prompt
from search.query.rewrite import _cluster_system, _shortlist, expand_abbrevs


def _glossary() -> dict:
    return {
        "terms": [
            {
                "canonical": "ORDER-HOLD",
                "colloquial": ["주문 보류", "결제 대기", "승인 대기", "처리 대기"],
                "meaning": "주문 승인을 기다리는 상태",
                "category": "업무 상태",
            },
            {
                "canonical": "PAYMENT-REVIEW",
                "colloquial": ["결제 검토"],
                "meaning": "결제를 검토하는 상태",
                "category": "업무 상태",
            },
        ],
        "actions": [],
    }


class GlossaryProductSearchTest(unittest.TestCase):
    def test_prompt_render_preserves_every_term_and_alias_once(self) -> None:
        rendered = render_for_prompt(_glossary())
        for value in (
            "ORDER-HOLD", "주문 보류", "결제 대기", "승인 대기", "처리 대기",
            "PAYMENT-REVIEW", "결제 검토",
        ):
            self.assertEqual(1, rendered.count(value))
        self.assertNotIn("동작 매핑", rendered)

    def test_cluster_prompt_does_not_repeat_a_candidate_section(self) -> None:
        with patch("search.query.rewrite.build_glossary", return_value=_glossary()):
            prompt = _cluster_system()
        self.assertEqual(1, prompt.count("ORDER-HOLD"))
        self.assertNotIn("유력 후보", prompt)

    def test_shortlist_and_expansion_do_not_apply_fixed_result_caps(self) -> None:
        terms = {
            "terms": [
                {
                    "canonical": f"SYS-{index}",
                    "colloquial": ["망"],
                    "meaning": "망 처리",
                    "category": "시스템",
                }
                for index in range(12)
            ],
            "actions": [],
        }
        with patch("search.query.rewrite.build_glossary", return_value=terms):
            self.assertEqual(12, len(_shortlist("망")))
            expanded = expand_abbrevs("망")
        self.assertEqual(12, len(expanded["terms"]))


if __name__ == "__main__":
    unittest.main()
