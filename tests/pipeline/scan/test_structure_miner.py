"""결정론 구조 마이너 계약 (spec 078, deep-research-2).

범용성 우선: 4계열(줄 레코드/블록/섹션/구분자 표) + 적대 케이스(헤더·주석 배제,
단일 파일 구조 배제, CRLF, key 접두 상수 분리)를 전부 검증한다. 모든 방출 후보는
production probe 로 재실행해 span 이 실제로 성립함을 함께 확인한다.
"""
from __future__ import annotations

import unittest

from pipeline.stages.scan.external_skills import probe_extraction_rule
from pipeline.stages.scan.structure_miner import (
    line_shape_profile,
    mine_rule_candidates,
)


class LineShapeProfileTest(unittest.TestCase):
    def test_dominant_shapes_only_and_small_files_keep_all(self) -> None:
        # 큰 파일(비공백 9줄↑)은 3회 미만 모양을 잡음으로 버린다.
        big = "".join(f"K{i}=1\n" for i in range(10)) + "산문 한 줄은 잡음이다\n"
        self.assertEqual(1, len(line_shape_profile(big)))
        # 작은 파일은 모든 줄이 유의미 — 한 줄짜리 레코드 파일도 프로파일을 가진다.
        self.assertEqual(1, len(line_shape_profile("ORDER.API=/orders\n")))

    def test_different_formats_have_disjoint_profiles(self) -> None:
        records = "".join(f"KEY{i:02d}.PATH=/v{i}\n" for i in range(5))
        table = "".join(f"row{i}|{i}|on\n" for i in range(5))
        self.assertEqual(
            frozenset(),
            line_shape_profile(records) & line_shape_profile(table),
        )


def _spans_ok(pattern: str, samples: list[str]) -> int:
    total = 0
    for sample in samples:
        for match in probe_extraction_rule(pattern, "key", sample):
            assert sample[match.start:match.end], "빈 span 금지"
            total += 1
    return total


class LineRecordMiningTest(unittest.TestCase):
    SAMPLES = [
        "* 헤더 주석\nTRX=ORD0001; NAME=주문생성; IO=IN;  LEN=120\nTRX=STK0101; NAME=재고조회; IO=OUT; LEN=200\n",
        "* 헤더 주석\nTRX=PAY0001; NAME=결제승인; IO=IN;  LEN=160\nTRX=PAY0002; NAME=결제취소; IO=IN;  LEN=160\n",
    ]

    def test_record_rule_is_mined_with_clean_key(self) -> None:
        candidates = mine_rule_candidates(self.SAMPLES)
        records = [c for c in candidates if c.kind == "line_record"]
        self.assertTrue(records)
        best = records[0]
        # ★ 상수 접두(TRX=)가 key 에 섞이지 않는다 — 실측했던 품질 결함의 구조적 해소
        self.assertIn("ORD0001", best.sample_keys)
        self.assertNotIn("TRX=ORD0001", best.sample_keys)
        self.assertEqual(4, _spans_ok(best.pattern, self.SAMPLES))

    def test_comment_lines_are_naturally_excluded(self) -> None:
        candidates = mine_rule_candidates(self.SAMPLES)
        for candidate in candidates:
            for sample in self.SAMPLES:
                for match in probe_extraction_rule(candidate.pattern, "key", sample):
                    self.assertNotIn("헤더 주석", sample[match.start:match.end])


class TableMiningTest(unittest.TestCase):
    SAMPLES = [
        "CODE|LABEL|USE\nGRD01|일반등급|Y\nGRD02|우수등급|Y\n",
        "CODE|LABEL|USE\nRGN01|서울권역|Y\nRGN09|제주권역|N\n",
    ]

    def test_header_row_is_excluded_and_keys_are_row_ids(self) -> None:
        candidates = mine_rule_candidates(self.SAMPLES)
        self.assertTrue(candidates)
        best = candidates[0]
        self.assertIn("GRD01", best.sample_keys)
        for sample in self.SAMPLES:
            for match in probe_extraction_rule(best.pattern, "key", sample):
                # ★ 헤더 행이 레코드로 오인되지 않는다 — 실측 결함 2의 구조적 해소
                self.assertNotEqual("CODE", match.key)


class BlockMiningTest(unittest.TestCase):
    SAMPLES = [
        "-- 정의\nBEGIN JOB DAILY_SETTLE\n  SCHEDULE 0300\n  TARGET settle_main\nEND JOB\n"
        "BEGIN JOB DAILY_CLEAN\n  SCHEDULE 0400\nEND JOB\n",
        "-- 정의\nBEGIN JOB MONTH_REPORT\n  SCHEDULE 0500\n  RETRY 3\nEND JOB\n",
    ]

    def test_open_close_block_is_mined_whole(self) -> None:
        candidates = mine_rule_candidates(self.SAMPLES)
        blocks = [c for c in candidates if c.kind == "block"]
        self.assertTrue(blocks)
        block = blocks[0]
        self.assertIn("MONTH_REPORT", block.sample_keys + tuple(
            key for c in blocks for key in c.sample_keys))
        for sample in self.SAMPLES:
            for match in probe_extraction_rule(block.pattern, "key", sample):
                text = sample[match.start:match.end]
                self.assertTrue(text.startswith("BEGIN JOB"))
                self.assertTrue(text.rstrip().endswith("END JOB"))


class SectionMiningTest(unittest.TestCase):
    SAMPLES = [
        ":::MAIN_MENU:::\ntitle=메인\norder=1\n\n:::SUB_MENU:::\ntitle=부가\norder=2\n",
        ":::ADMIN_MENU:::\ntitle=관리\norder=9\n",
    ]

    def test_section_candidate_spans_header_to_next_header(self) -> None:
        candidates = mine_rule_candidates(self.SAMPLES)
        sections = [c for c in candidates if c.kind == "section"]
        self.assertTrue(sections)
        section = sections[0]
        matches = probe_extraction_rule(section.pattern, "key", self.SAMPLES[0])
        self.assertEqual(["MAIN_MENU", "SUB_MENU"], [m.key for m in matches])
        first = self.SAMPLES[0][matches[0].start:matches[0].end]
        self.assertIn("title=메인", first)          # 본문 포함
        self.assertNotIn("SUB_MENU", first)         # 다음 섹션 미침범


class WidenedVariantTest(unittest.TestCase):
    def test_single_file_constant_prefix_gets_generalized_variant(self) -> None:
        # 파일이 1개뿐이면 GRD 가 상수인지 이 파일의 우연인지 알 수 없다 —
        # base(GRD 리터럴)와 넓힌 변형(문자류 일반화)을 모두 후보로 낸다.
        samples = ["CODE|LABEL\nGRD01|일반\nGRD02|우수\nGRD03|특수\n"]
        candidates = mine_rule_candidates(samples)
        patterns = [c.pattern for c in candidates]
        self.assertTrue(any("GRD" in p for p in patterns), patterns)
        widened = [c for c in candidates if "일반화 변형" in c.note]
        self.assertTrue(widened)
        # 넓힌 변형은 다른 접두(RGN09)도 매치해야 한다
        hits = probe_extraction_rule(widened[0].pattern, "key", "RGN09|제주\n")
        self.assertEqual(["RGN09"], [m.key for m in hits])


class AdversarialTest(unittest.TestCase):
    def test_every_verified_structure_candidate_remains_available(self) -> None:
        separators = ("=", ":", "|", ",", ";", "@", "#", "~")
        samples = []
        for file_index in range(2):
            lines = [
                f"K{group}{file_index}{item}{separator}VALUE{item}"
                for group, separator in enumerate(separators)
                for item in range(3)
            ]
            samples.append("\n".join(lines) + "\n")

        candidates = mine_rule_candidates(samples)

        self.assertGreater(len(candidates), 6)
        self.assertTrue(any(r"\~" in candidate.pattern for candidate in candidates))

    def test_single_file_only_structure_is_not_generalized(self) -> None:
        samples = [
            "AAA=1\nAAA=2\nAAA=3\n",                 # 파일1에만 있는 구조
            "zzz: other\nzzz: more\nzzz: extra\n",   # 파일2에만 있는 구조
        ]
        for candidate in mine_rule_candidates(samples):
            self.assertGreaterEqual(len(candidate.sample_keys), 1)
        # 두 파일 교차 반복이 없으므로 line_record 후보는 없어야 한다
        kinds = {c.kind for c in mine_rule_candidates(samples)}
        self.assertNotIn("line_record", kinds)

    def test_crlf_lines_still_match(self) -> None:
        samples = [
            "ID=AB01; V=x\r\nID=CD02; V=y\r\n",
            "ID=EF33; V=z\r\nID=GH44; V=w\r\n",
        ]
        candidates = mine_rule_candidates(samples)
        self.assertTrue(candidates)
        self.assertEqual(4, _spans_ok(candidates[0].pattern, samples))

    def test_no_viable_key_yields_no_candidate(self) -> None:
        samples = ["x=1\nx=2\nx=3\n", "x=4\nx=5\n"]   # 변수가 한 자리 숫자뿐 (식별자 아님)
        self.assertEqual([], mine_rule_candidates(samples))

    def test_unclosed_block_is_not_mined(self) -> None:
        samples = [
            "BEGIN A\nbody\nEND\nBEGIN B\nbody\n",   # 두 번째 블록이 안 닫힘
            "BEGIN C\nbody\nEND\n",
        ]
        kinds = {c.kind for c in mine_rule_candidates(samples)}
        self.assertNotIn("block", kinds)


if __name__ == "__main__":
    unittest.main()
