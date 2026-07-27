"""별칭 후보 결정론 계약 (spec 079) — 닫힌 선택으로 나열 폭주 구조 차단."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from pipeline.models.code_analysis_output import GlossarySynthesisOutput
from pipeline.steps.step8_post_processing.build_glossary_step import (
    _accept,
    _alias_candidates,
    _build_contexts,
)


def _terms(*names: str) -> dict[str, dict]:
    return {n.lower(): {"canonical": n, "meanings": []} for n in names}


class AliasCandidatesTest(unittest.TestCase):
    def test_only_exact_normalized_typography_variants_are_candidates(self) -> None:
        terms = _terms(
            "일련번호",
            "주민일련번호",
            "주민등록일련번호",
            "은행코드",
            "CJ-EXP",
            "cj_exp",
            "cjexp_yn",
        )
        self.assertEqual(
            [], _alias_candidates("일련번호", terms),
        )
        self.assertEqual(["cj_exp"], _alias_candidates("cj-exp", terms))
        self.assertNotIn("cjexp_yn", _alias_candidates("cj-exp", terms))
        # 무관 용어는 후보 자체가 없다
        self.assertEqual([], _alias_candidates("은행코드", terms))

    def test_related_entity_names_are_not_aliases_by_substring(self) -> None:
        terms = _terms(
            "orders",
            "orders_select",
            "orders_insert",
            "orders_request",
            "load_orders",
        )
        self.assertEqual([], _alias_candidates("orders", terms))

    def test_short_terms_and_cap(self) -> None:
        terms = _terms("id", "svc_id", *[f"일련번호{i:02d}" for i in range(40)], "일련번호")
        # 2자 이하 표기는 아무 데나 포함되므로 후보에서 제외
        self.assertEqual([], _alias_candidates("id", terms))
        self.assertLessEqual(len(_alias_candidates("일련번호", terms)), 30)


class AcceptCandidateScopeTest(unittest.TestCase):
    def test_aliases_outside_candidates_are_purged(self) -> None:
        out = GlossarySynthesisOutput(
            meaning="고유 식별 번호", aliases="주민일련번호, 은행코드",
            evidence="고유 식별 번호를 사용한다.",
        )
        res = _accept(
            out, "이 루틴은 고유 식별 번호를 사용한다.",
            {"주민일련번호"}, "일련번호",
        )
        self.assertEqual(["주민일련번호"], res["aliases"])

    def test_blank_or_foreign_evidence_rejects_synthesis(self) -> None:
        blank = GlossarySynthesisOutput(
            meaning="근거 없는 뜻", aliases="", evidence="",
        )
        foreign = GlossarySynthesisOutput(
            meaning="근거 없는 뜻", aliases="", evidence="다른 문맥의 문장",
        )
        self.assertIsNone(_accept(blank, "실제 문맥", set(), "표제어"))
        self.assertIsNone(_accept(foreign, "실제 문맥", set(), "표제어"))


class SourceEvidenceContextTest(unittest.TestCase):
    def test_generated_semantic_fields_are_not_reused_as_glossary_evidence(self) -> None:
        node = SimpleNamespace(
            file_path="src/order.py",
            start_line=10,
            end_line=14,
            comment="ZX는 배송 상태 교환 규격이다.",
            code_text='if mode == "ZX":\n    send(payload)',
            summary="ZX는 결제 승인 코드다.",
            search_text="결제 승인 실패를 검색한다.",
        )
        terms = {
            "zx": {
                "canonical": "ZX",
                "meanings": ["후보 의미"],
                "src": [node],
            }
        }

        context = _build_contexts(terms)["zx"]

        self.assertIn("ZX는 배송 상태 교환 규격이다.", context)
        self.assertIn('mode == "ZX"', context)
        self.assertNotIn(node.summary, context)
        self.assertNotIn(node.search_text, context)


if __name__ == "__main__":
    unittest.main()
