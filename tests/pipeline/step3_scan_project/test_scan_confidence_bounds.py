import unittest

from pipeline.steps.step3_scan_project.scan_agent import _apply_verdict
from pipeline.steps.step4_linking.link_tables.regex_table_linker import PatternVerdict


class ScanConfidenceBoundsTests(unittest.TestCase):
    def test_verified_multi_edge_site_confidence_is_bounded(self) -> None:
        verdict = PatternVerdict(ok=True, resolved_ratio=1.47, summary="multi-edge site")

        confidence, reason = _apply_verdict(verdict, [object()], None)

        self.assertEqual(1.0, confidence)
        self.assertIsNone(reason)

    def test_verified_low_coverage_keeps_documented_floor(self) -> None:
        verdict = PatternVerdict(ok=True, resolved_ratio=0.12, summary="low coverage")

        confidence, _ = _apply_verdict(verdict, [object()], None)

        self.assertEqual(0.5, confidence)
