"""verify_external_rule 도구·probe 엔진 계약 (spec 078 — Agent 검증 도구)."""
from __future__ import annotations

import unittest

from agents.tools.validation import make_verify_external_rule_tool
from pipeline.steps.step4_scan_project.external_skills import (
    ExternalSkillValidationError,
    probe_extraction_rule,
)


SAMPLE = "# header\nAAA=1; x\nBBB=2; y\n"
DECOY = "<xml><a>1</a></xml>"


class ProbeExtractionRuleTest(unittest.TestCase):
    def test_probe_returns_production_spans(self) -> None:
        matches = probe_extraction_rule(r"^(?P<key>[A-Z]{3})=\d;.*$", "key", SAMPLE)
        self.assertEqual(["AAA", "BBB"], [m.key for m in matches])
        for match in matches:
            self.assertEqual(SAMPLE[match.start:match.end],
                             SAMPLE[match.start:match.end].strip("\n"))

    def test_probe_rejects_unsafe_and_bad_fields(self) -> None:
        with self.assertRaises(ExternalSkillValidationError):
            probe_extraction_rule(r"(a+)*b(?P<key>x)", "key", SAMPLE)     # 중첩 수량자
        with self.assertRaises(ExternalSkillValidationError):
            probe_extraction_rule(r"(?P<key>[A-Z]+)", "bad-name", SAMPLE)  # 그룹명 하이픈


class VerifyExternalRuleToolTest(unittest.TestCase):
    def test_tool_reports_matches_and_negative_hits(self) -> None:
        tool = make_verify_external_rule_tool([SAMPLE], [DECOY, SAMPLE])
        report = tool.invoke({"pattern": r"^(?P<key>[A-Z]{3})=\d;.*$"})
        self.assertIn("매치 2건", report)
        self.assertIn("key='AAA'", report)
        self.assertIn("chars=", report)
        self.assertNotIn("span=[", report)
        self.assertNotIn("(파일", report)
        self.assertEqual(
            {"pattern"},
            set(tool.args_schema.model_json_schema()["properties"]),
        )
        # SAMPLE 자신이 negative 에도 있으므로 과잉 매치 경고가 나와야 한다.
        self.assertIn("NEGATIVE_MATCH", report)

    def test_tool_reports_rejection_reason(self) -> None:
        tool = make_verify_external_rule_tool([SAMPLE], [])
        report = tool.invoke({"pattern": r"(?P<key>x?)"})
        self.assertTrue(report.startswith("REJECTED:"))


if __name__ == "__main__":
    unittest.main()
