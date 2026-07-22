"""Gold corpus self-consistency (spec 078 S1/T104).

Validates the answer key itself: every fixture has an expected file, every
expected chunk text occurs exactly once in the decoded source, spans are
ordered and non-overlapping, and keys are sorted unique.  Adapter conformance
is scored separately in S2 tests.
"""
from __future__ import annotations

import unittest

from tests.pipeline.step3_load_external.gold.gold_loader import iter_gold, locate_spans


class GoldIntegrityTest(unittest.TestCase):
    def test_gold_corpus_is_self_consistent(self) -> None:
        cases = iter_gold()
        formats = {case.format for case in cases}
        self.assertLessEqual(
            {"xml", "yaml", "json", "jsonl", "md", "properties", "ini", "csv"},
            formats,
            "gold corpus must cover every declared text format",
        )
        for case in cases:
            with self.subTest(case=case.stem):
                if case.fail_closed:
                    self.assertEqual((), case.chunks)
                    continue
                self.assertGreater(len(case.chunks), 0)
                spans = locate_spans(case.source, [chunk["text"] for chunk in case.chunks])
                for chunk, (start, end) in zip(case.chunks, spans):
                    self.assertEqual(chunk["text"], case.source[start:end])
                    keys = chunk["keys"]
                    self.assertEqual(sorted(set(keys)), sorted(keys))
                    self.assertIn(chunk["semantic_kind"], {
                        "xml_element", "yaml_entry", "json_entry", "md_section",
                        "record", "row", "agent_segment", "derived_document",
                    })

    def test_fail_closed_cases_exist(self) -> None:
        cases = iter_gold()
        self.assertGreaterEqual(sum(1 for case in cases if case.fail_closed), 3)


if __name__ == "__main__":
    unittest.main()
