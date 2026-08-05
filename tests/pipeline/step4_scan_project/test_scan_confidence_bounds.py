import unittest

from agents.scan import _apply_verdict
from pipeline.steps.step5_linking.link_tables.regex_table_linker import PatternVerdict


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

    def test_proposed_rule_verification_failure_is_fatal(self) -> None:
        verdict = PatternVerdict(
            ok=False,
            resolved_ratio=0.0,
            summary="dead rule",
            failed_rule_ids=("dead",),
        )

        with self.assertRaisesRegex(RuntimeError, "dead rule"):
            _apply_verdict(verdict, [object()], None)

    def test_explicit_no_rule_path_remains_non_fatal(self) -> None:
        verdict = PatternVerdict(ok=False, resolved_ratio=0.0, summary="no rules")

        confidence, reason = _apply_verdict(verdict, [], "no common access syntax")

        self.assertEqual(0.1, confidence)
        self.assertEqual("no common access syntax", reason)
