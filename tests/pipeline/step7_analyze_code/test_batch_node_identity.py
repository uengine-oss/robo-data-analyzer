from __future__ import annotations

import unittest

from pipeline.models.analysis_context import AnalysisContext
from pipeline.steps.step7_analyze_code.batch_node_identity import batch_node_keys


def _context(node_id: str, start: int, end: int) -> AnalysisContext:
    return AnalysisContext(
        id=node_id, signature=node_id, file_path="sample.sql",
        start_line=start, end_line=end,
    )


class BatchNodeIdentityTest(unittest.TestCase):
    def test_unique_coordinates_keep_compact_legacy_keys(self) -> None:
        contexts = {"a": _context("a", 1, 2), "b": _context("b", 3, 4)}
        self.assertEqual({"1-2": "a", "3-4": "b"}, batch_node_keys(("a", "b"), contexts))

    def test_same_range_nodes_are_disambiguated_by_full_identity_digest(self) -> None:
        contexts = {"outer": _context("outer", 25, 25), "inner": _context("inner", 25, 25)}
        got = batch_node_keys(("outer", "inner"), contexts)
        self.assertEqual({"outer", "inner"}, set(got.values()))
        self.assertEqual(2, len(got))
        self.assertTrue(all(key.startswith("25-25#") for key in got))


if __name__ == "__main__":
    unittest.main()
