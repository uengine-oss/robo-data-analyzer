from __future__ import annotations

import unittest

from pipeline.models.code_analysis_output import RuleItem
from pipeline.quality_gates.example_synthesis import synthesize_examples


def _rule(anchor: int, condition: str | None, effects=()) -> RuleItem:
    return RuleItem(
        anchor_line=anchor,
        structural_condition=condition,
        structural_effects=[],
        business_effects=list(effects),
    )


class ExactRuleExampleProjectionTest(unittest.TestCase):
    def test_condition_and_effects_are_reused_byte_exactly(self) -> None:
        condition = 'not (F("status") == F("READY")) and F("retry") < 3'
        effects = (
            'meaning_call("reject", None, [], "요청을 거절한다")',
            'meaning_return(F("RC_ERR"), "오류를 반환한다")',
        )

        [example] = synthesize_examples([_rule(17, condition, effects)])[17]

        self.assertEqual([condition], example["given_conditions"])
        self.assertEqual(list(effects), example["expected_effects"])

    def test_condition_is_not_reparsed_or_normalized(self) -> None:
        opaque = 'F("code == 1 || code == 2")'
        [example] = synthesize_examples([_rule(1, opaque, ('ret("RC")',))])[1]
        self.assertEqual([opaque], example["given_conditions"])

    def test_unconditional_rule_has_no_example(self) -> None:
        self.assertEqual([], synthesize_examples([
            _rule(9, None, ('meaning_return(F("RC_NRM"), "정상 반환")',)),
        ])[9])

    def test_effectless_rule_has_no_example(self) -> None:
        self.assertEqual([], synthesize_examples([
            _rule(7, 'F("ready")', ()),
        ])[7])


if __name__ == "__main__":
    unittest.main()
