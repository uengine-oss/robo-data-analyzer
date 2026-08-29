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
    _effective_timeout,
    apply_validated_external_skill,
    load_external_skills,
    persist_external_skill,
    validate_external_skill,
)


def _skill(pattern: str = r"(?m)^CFG\[(?P<key>[A-Za-z][\w.]*)\]\s*=\s*(?P<value>.+)$") -> ExternalSkill:
    return ExternalSkill(
        name="bracket-config",
        extensions=(".cfgx",),
        rules=(ExternalExtractionRule(
            name="config_entry", pattern=pattern, key_group="key",
        ),),
    )


class ExternalSkillContractTest(unittest.TestCase):
    def test_schema_forbids_extra_fields_and_bad_names(self) -> None:
        with self.assertRaises(ValidationError):
            ExternalSkill.model_validate({
                **_skill().model_dump(),
                "expected_customer_keys": ["shop.secret"],
            })
        with self.assertRaises(ValidationError):
            ExternalSkill.model_validate({
                **_skill().model_dump(),
                "content_signatures": ["CFG["],
            })
        with self.assertRaises(ValidationError):
            ExternalExtractionRule.model_validate({
                **_skill().rules[0].model_dump(),
                "value_group": "value",
            })
        with self.assertRaises(ValidationError):
            ExternalExtractionRule(name="bad-name", pattern="x", key_group="key")

    def test_schema_does_not_reject_complete_format_by_rule_or_extension_count(self) -> None:
        rules = tuple(
            ExternalExtractionRule(
                name=f"record_{index}",
                pattern=rf"(?P<key>K{index}\d+)=x;",
                key_group="key",
            )
            for index in range(13)
        )

        skill = ExternalSkill(
            name="many-record-forms",
            extensions=tuple(f".x{index}" for index in range(13)),
            rules=rules,
        )

        self.assertEqual(13, len(skill.extensions))
        self.assertEqual(13, len(skill.rules))

    def test_validated_application_is_exact(self) -> None:
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
            self.assertEqual([], list(root.glob("**/*.tmp")))

    def test_extensionless_skill_cannot_enter_the_reusable_catalog(self) -> None:
        run_only = _skill().model_copy(update={"extensions": ()})
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(
            ExternalSkillValidationError, "run-only",
        ):
            persist_external_skill(run_only, Path(temp))

    def test_repeated_keys_up_to_one_match_per_line_are_allowed(self) -> None:
        # 유일 key 가 몇 종뿐인 형식(등급/코드류)은 정상 데이터다 — 줄 수를 넘지
        # 않는 반복 매치는 폭주가 아니다 (감사 반영).
        text = "".join(f"CFG[level.{i % 3}] = x\n" for i in range(150))
        validated = validate_external_skill(_skill(), [text])
        self.assertEqual(150, len(apply_validated_external_skill(validated, text)))

    def test_complete_compact_records_are_not_rejected_by_match_count(self) -> None:
        text = "".join(f"K{index}=x;" for index in range(1001))
        compact = _skill(r"(?P<key>K\d+)=(?P<value>x);")

        validated = validate_external_skill(compact, [text])

        self.assertEqual(1001, len(apply_validated_external_skill(validated, text)))

    def test_long_exact_key_is_not_rejected_by_character_budget(self) -> None:
        key = "K" * 241
        text = f"CFG[{key}] = exact"

        validated = validate_external_skill(_skill(), [text])

        self.assertEqual(
            [key], [match.key for match in apply_validated_external_skill(validated, text)]
        )

    def test_positive_source_with_uncovered_non_whitespace_is_rejected(self) -> None:
        source = (
            "CFG[shop.title] = exact\n"
            "ALT{shop.locale}: ko\n"
        )

        with self.assertRaisesRegex(
            ExternalSkillValidationError,
            "uncovered source.*ALT",
        ):
            validate_external_skill(_skill(), [source])

    def test_default_timeout_scales_with_the_complete_input_without_fixed_ceiling(self) -> None:
        self.assertAlmostEqual(0.25, _effective_timeout(0))
        self.assertLess(_effective_timeout(21_269), _effective_timeout(5_000_000))
        self.assertGreater(_effective_timeout(10**9), 5.0)

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
