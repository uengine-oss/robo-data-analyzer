"""별칭 후보 결정론 계약 (spec 079) — 닫힌 선택으로 나열 폭주 구조 차단."""
from __future__ import annotations

import unittest

from pipeline.models.code_analysis_output import GlossarySynthesisOutput
from pipeline.steps.step8_post_processing.build_glossary_step import (
    _accept,
    _alias_candidates,
)


def _terms(*names: str) -> dict[str, dict]:
    return {n.lower(): {"canonical": n, "meanings": []} for n in names}


class AliasCandidatesTest(unittest.TestCase):
    def test_variants_by_normalized_containment_only(self) -> None:
        terms = _terms("일련번호", "주민일련번호", "주민등록일련번호", "은행코드", "CJ-EXP", "cjexp_yn")
        self.assertEqual(
            ["주민일련번호", "주민등록일련번호"], _alias_candidates("일련번호", terms),
        )
        # 하이픈·언더스코어 표기차도 정규화 포함으로 잡힌다 (변수/플래그명 규약)
        self.assertEqual(["cjexp_yn"], _alias_candidates("cj-exp", terms))
        # 무관 용어는 후보 자체가 없다
        self.assertEqual([], _alias_candidates("은행코드", terms))

    def test_short_terms_and_cap(self) -> None:
        terms = _terms("id", "svc_id", *[f"일련번호{i:02d}" for i in range(40)], "일련번호")
        # 2자 이하 표기는 아무 데나 포함되므로 후보에서 제외
        self.assertEqual([], _alias_candidates("id", terms))
        self.assertLessEqual(len(_alias_candidates("일련번호", terms)), 30)


class AcceptCandidateScopeTest(unittest.TestCase):
    def test_aliases_outside_candidates_are_purged(self) -> None:
        out = GlossarySynthesisOutput(
            meaning="고유 식별 번호", aliases="주민일련번호, 은행코드", evidence="",
        )
        res = _accept(out, "문맥", {"주민일련번호"}, "일련번호")
        self.assertEqual(["주민일련번호"], res["aliases"])


if __name__ == "__main__":
    unittest.main()
