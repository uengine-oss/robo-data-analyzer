"""sample 탐색 도구 계약 (spec 078 — 큰 파일 대응)."""
from __future__ import annotations

import unittest

from agents.tools.mining import make_structure_candidate_tool
from agents.tools.sampling import _effective_grep_timeout, make_sample_exploration_tools
from agents.tools.validation import make_verify_external_rule_tool


BIG = ("REC|A%03d|x\n" % 1) * 1 + "".join(f"REC|A{i:03d}|x\n" for i in range(2, 300)) \
    + "".join(f"EVT B{i:03d} {{}}\n" for i in range(50))


class SampleExplorationToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.overview, self.read_range, self.grep = make_sample_exploration_tools([BIG])

    def test_overview_reveals_both_shapes_with_line_ranges(self) -> None:
        report = self.overview.invoke({})
        # 문자 뭉치는 A, 숫자 뭉치는 9 로 접힌 골격이 구조 2종을 드러낸다.
        self.assertIn("A|A9|A", report)
        self.assertIn("A A9 {}", report)
        # 뒤쪽 구조(EVT)의 시작 줄이 앞 구조 범위 밖임이 드러나야 한다.
        self.assertIn("300줄", report)

    def test_grep_timeout_scales_with_complete_input_without_fixed_ceiling(self) -> None:
        self.assertLess(_effective_grep_timeout(1_000), _effective_grep_timeout(1_000_000))
        self.assertGreater(_effective_grep_timeout(10**9), 5.0)

    def test_read_range_returns_requested_window(self) -> None:
        report = self.read_range.invoke({"file_index": 0, "start_line": 300})
        self.assertIn("EVT B", report)
        self.assertIn("300", report)
        self.assertIn("INVALID", self.read_range.invoke({"file_index": 9, "start_line": 1}))

    def test_grep_lists_hits_with_lines(self) -> None:
        report = self.grep.invoke({"pattern": r"^EVT \w+"})
        self.assertIn("returned=20 total=50 next_offset=20", report)
        self.assertIn("300줄", report)
        last = self.grep.invoke({"pattern": r"^EVT \w+", "offset": 40})
        self.assertIn("returned=10 total=50 next_offset=null", last)
        self.assertIn("INVALID_REGEX", self.grep.invoke({"pattern": "("}))

    def test_overview_pages_include_training_files_after_the_old_three_file_cap(self) -> None:
        tools = make_sample_exploration_tools([
            f"REC|F{index}|x\n" for index in range(23)
        ])
        first = tools[0].invoke({"offset": 0})
        second = tools[0].invoke({"offset": 20})
        self.assertIn("returned=20 total=23 next_offset=20", first)
        self.assertIn("returned=3 total=23 next_offset=null", second)
        self.assertIn("sample[22]", second)

    def test_verify_warns_about_unmatched_tail(self) -> None:
        verify = make_verify_external_rule_tool([BIG], [])
        report = verify.invoke({"pattern": r"^REC\|(?P<key>\w+)\|.*$", "key_group": "key"})
        self.assertIn("매치 299건", report)
        self.assertIn("매치가 없다", report)          # 뒤쪽 EVT 구간 미매치 경고
        self.assertIn("read_sample_range(0,", report)

    def test_structure_candidates_are_paginated_without_hiding_later_candidates(self) -> None:
        separators = ("=", ":", "|", ",", ";", "@", "#", "~")
        samples = []
        for file_index in range(2):
            lines = [
                f"K{group}{file_index}{item}{separator}VALUE{item}"
                for group, separator in enumerate(separators)
                for item in range(3)
            ]
            samples.append("\n".join(lines) + "\n")

        candidates = make_structure_candidate_tool(samples)
        first = candidates.invoke({"offset": 0})
        second = candidates.invoke({"offset": 8})
        third = candidates.invoke({"offset": 16})

        self.assertIn("returned=8", first)
        self.assertIn("next_offset=8", first)
        self.assertNotIn("returned=0", second)
        self.assertIn("next_offset=16", second)
        self.assertIn("next_offset=null", third)
        self.assertIn(r"\~", third)


if __name__ == "__main__":
    unittest.main()
