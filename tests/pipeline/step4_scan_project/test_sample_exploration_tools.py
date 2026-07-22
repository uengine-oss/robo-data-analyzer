"""sample 탐색 도구 계약 (spec 078 — 큰 파일 대응)."""
from __future__ import annotations

import unittest

from agents.tools.sampling import make_sample_exploration_tools
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

    def test_read_range_returns_requested_window(self) -> None:
        report = self.read_range.invoke({"file_index": 0, "start_line": 300})
        self.assertIn("EVT B", report)
        self.assertIn("300", report)
        self.assertIn("INVALID", self.read_range.invoke({"file_index": 9, "start_line": 1}))

    def test_grep_lists_hits_with_lines(self) -> None:
        report = self.grep.invoke({"pattern": r"^EVT \w+"})
        self.assertIn("총 50건", report)
        self.assertIn("300줄", report)
        self.assertIn("INVALID_REGEX", self.grep.invoke({"pattern": "("}))

    def test_verify_warns_about_unmatched_tail(self) -> None:
        verify = make_verify_external_rule_tool([BIG], [])
        report = verify.invoke({"pattern": r"^REC\|(?P<key>\w+)\|.*$", "key_group": "key"})
        self.assertIn("매치 299건", report)
        self.assertIn("매치가 없다", report)          # 뒤쪽 EVT 구간 미매치 경고
        self.assertIn("read_sample_range(0,", report)


if __name__ == "__main__":
    unittest.main()
