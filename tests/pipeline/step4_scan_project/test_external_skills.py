from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from pydantic import ValidationError

from pipeline.steps.step4_scan_project.external_skills import (
    ExternalExtractionRule,
    ExternalSkill,
    ExternalSkillValidationError,
    apply_validated_external_skill,
    load_external_skills,
    persist_external_skill,
    skill_matches_file,
    validate_external_skill,
)


def _skill(pattern: str = r"(?m)^CFG\[(?P<key>[A-Za-z][\w.]*)\]\s*=\s*(?P<value>.+)$") -> ExternalSkill:
    return ExternalSkill(
        name="bracket-config",
        extensions=(".cfgx",),
        content_signatures=("CFG[",),
        rules=(ExternalExtractionRule(
            name="config_entry", pattern=pattern, key_group="key", value_group="value",
        ),),
        explanation="generic bracket config",
        provenance="deterministic test fixture",
    )


class ExternalSkillContractTest(unittest.TestCase):
    def test_schema_forbids_extra_fields_and_bad_names(self) -> None:
        with self.assertRaises(ValidationError):
            ExternalSkill.model_validate({
                **_skill().model_dump(),
                "expected_customer_keys": ["shop.secret"],
            })
        with self.assertRaises(ValidationError):
            ExternalExtractionRule(name="bad-name", pattern="x", key_group="key")

    def test_validated_application_is_bounded_and_exact(self) -> None:
        positive = "CFG[shop.title] = 쇼핑몰\nCFG[shop.locale] = ko"
        validated = validate_external_skill(
            _skill(), [positive], negative_samples=["# CFG[decoy] = no"],
        )
        matches = apply_validated_external_skill(validated, positive)
        self.assertEqual(["shop.title", "shop.locale"], [match.key for match in matches])
        self.assertEqual([1, 2], [match.line for match in matches])

    def test_unsafe_timeout_empty_overfit_and_negative_rules_are_rejected(self) -> None:
        cases = (
            (_skill(r"(?P<key>(a+)+)$"), "a" * 10000 + "!", [], "unsafe"),
            (_skill(r"NO_MATCH_(?P<key>\w+)"), "CFG[x]=1", [], "no keys"),
            (_skill(r"CFG\[(?P<key>shop\.title)\]"), "CFG[shop.title]", [], "enumerates"),
            (_skill(), "CFG[good.key] = yes", ["CFG[decoy.key] = no"], "negative"),
        )
        for skill, positive, negatives, message in cases:
            with self.subTest(message=message), self.assertRaises(ExternalSkillValidationError):
                validate_external_skill(skill, [positive], negative_samples=negatives, timeout_seconds=0.001)

    def test_atomic_round_trip_and_catalog_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "external"
            target = persist_external_skill(_skill(), root)
            self.assertTrue(target.is_file())
            self.assertEqual([_skill()], load_external_skills(root))
            self.assertTrue(skill_matches_file(_skill(), ".cfgx", "prefix CFG[x]"))
            self.assertFalse(skill_matches_file(_skill(), ".java", "CFG[x]"))
            self.assertEqual([], list(root.glob("**/*.tmp")))

    def test_regex_timeout_leaves_no_worker(self) -> None:
        before = {thread.ident for thread in threading.enumerate()}
        expensive = _skill(r"(?P<key>(?:a|aa)+)(?P<value>z?)$")
        with self.assertRaisesRegex(ExternalSkillValidationError, "regex timeout"):
            validate_external_skill(
                expensive,
                ["a" * 50_000 + "!"],
                timeout_seconds=0.001,
            )
        self.assertEqual(before, {thread.ident for thread in threading.enumerate()})


if __name__ == "__main__":
    unittest.main()
