import unittest

from agents.scan import _require_verified_table_access
from pipeline.steps.step5_linking.link_tables.regex_table_linker import PatternVerdict


class VerifiedTableAccessTests(unittest.TestCase):
    def test_verified_rule_is_accepted_without_model_confidence(self) -> None:
        verdict = PatternVerdict(ok=True, resolved_ratio=1.47, summary="multi-edge site")

        reason = _require_verified_table_access(verdict, [object()])

        self.assertIsNone(reason)

    def test_verified_rule_does_not_preserve_model_prose(self) -> None:
        verdict = PatternVerdict(ok=True, resolved_ratio=0.12, summary="low coverage")

        reason = _require_verified_table_access(verdict, [object()])

        self.assertIsNone(reason)

    def test_proposed_rule_verification_failure_is_fatal(self) -> None:
        verdict = PatternVerdict(
            ok=False,
            resolved_ratio=0.0,
            summary="dead rule",
            invalid_access_rule_ids=("dead",),
        )

        with self.assertRaisesRegex(RuntimeError, "dead rule"):
            _require_verified_table_access(verdict, [object()])

    def test_explicit_no_rule_path_remains_non_fatal(self) -> None:
        verdict = PatternVerdict(ok=False, resolved_ratio=0.0, summary="no rules")

        reason = _require_verified_table_access(verdict, [])

        self.assertEqual("table_access_patterns 미산출", reason)
