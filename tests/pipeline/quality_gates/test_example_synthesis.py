# -*- coding: utf-8 -*-
"""spec 116 §21 — Rule → Example 결정론 합성 계약 (헌법 I: LLM 0).

실측 근거: 전체 run 의 EXAMPLE 3,319건 중 71.9% 가 cond/then 의 기계적 재진술이었고
나머지도 여집합 부정이거나 창작이었다. 합성기는 그 사실만 만들고 지어내지 않는다.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pipeline.models.code_analysis_output import ExampleItem, RuleItem
from pipeline.quality_gates import example_synthesis
from pipeline.quality_gates.example_synthesis import synthesize_examples


def _rule(anchor, cond, then=()):
    return RuleItem(anchor_line=anchor, cond=cond, then=list(then), nl="")


class SynthesisTable(unittest.TestCase):
    """표기는 ``ast.unparse`` 정규형(따옴표 통일)이고 F() 안 원문은 보존된다."""

    def test_condition_without_effect_does_not_create_empty_outcome_example(self):
        self.assertEqual({7: []}, synthesize_examples([
            _rule(7, 'F("flag") == "Y"', []),
        ]))

    def test_and_condition_expands_every_term(self):
        rules = [_rule(1, 'F("a") == "01" and F("b") != "Z"', ['ret("RC_ERR")'])]
        got = synthesize_examples(rules)[1]
        self.assertEqual("satisfy", got[0]["kind"])
        self.assertEqual(["F('a') == '01'", "F('b') != 'Z'"], got[0]["given"])
        self.assertEqual(['ret("RC_ERR")'], got[0]["then"])

    def test_or_condition_takes_one_satisfying_path(self):
        rules = [_rule(1, 'F("a") == "03" or F("a") == "003"', ['ret("RC_NRM")'])]
        got = synthesize_examples(rules)[1]
        self.assertEqual(["F('a') == '03'"], got[0]["given"])

    def test_emits_only_one_satisfying_scenario(self):
        rules = [_rule(1, 'F("a") == "01"', ['ret("RC_ERR")'])]
        got = synthesize_examples(rules)[1]
        self.assertEqual(1, len(got))
        self.assertEqual("satisfy", got[0]["kind"])
        self.assertEqual(["F('a') == '01'"], got[0]["given"])
        self.assertEqual(['ret("RC_ERR")'], got[0]["then"])

    def test_else_of_resolves_to_negated_sibling_condition(self):
        rules = [
            _rule(512, 'F("bank") == "03"', ['ret("RC_NRM")']),
            _rule(518, 'else_of("IF@512")', ['ret("RC_ERR")']),
        ]
        got = synthesize_examples(rules)[518]
        self.assertEqual(["F('bank') != '03'"], got[0]["given"])
        self.assertEqual(['ret("RC_ERR")'], got[0]["then"])

    def test_else_if_chain_negates_each_preceding_condition(self):
        # 드모르간 계약: else-if 3단의 마지막 행은 앞 조건을 **전부 부정**한 상태다.
        # NOT(A and B) 를 (not A and not B) 로 접으면 정반대 분기가 나온다(합성 검수
        # 에서 실제로 잡은 결함 — 524 가 'bank == 03' 을 내놓았다).
        rules = [
            _rule(512, 'F("bank") == "03"', ['ret("RC_03")']),
            _rule(518, 'else_of("IF@512") and F("bank") == "71"', ['ret("RC_71")']),
            _rule(524, 'else_of("IF@518")', ['ret("RC_ERR")']),
        ]
        synthesized = synthesize_examples(rules)
        self.assertEqual(
            ["F('bank') != '03'", "F('bank') == '71'"],
            synthesized[518][0]["given"],
        )
        self.assertEqual(
            ["F('bank') != '03'", "F('bank') != '71'"],
            synthesized[524][0]["given"],
        )

    def test_negating_or_condition_negates_every_disjunct(self):
        rules = [
            _rule(512, 'F("a") == "03" or F("a") == "003"', []),
            _rule(518, 'else_of("IF@512")', ['ret("RC_ERR")']),
        ]
        got = synthesize_examples(rules)[518]
        self.assertEqual(["F('a') != '03'", "F('a') != '003'"], got[0]["given"])

    def test_unconditional_rule_has_no_examples(self):
        self.assertEqual([], synthesize_examples([_rule(9, None, ['ret("RC_NRM")'])])[9])

    def test_unknown_reference_invents_nothing(self):
        # 짝 규칙이 없으면(그 행이 거절·누락) 사실을 만들 수 없다 — 빈 목록.
        got = synthesize_examples([_rule(518, 'else_of("IF@512")', ['ret("x")'])])[518]
        self.assertEqual([], got)

    def test_self_reference_does_not_recurse_forever(self):
        got = synthesize_examples([_rule(512, 'else_of("IF@512")', [])])[512]
        self.assertEqual([], got)

    def test_long_else_chain_resolves_each_row_a_bounded_number_of_times(self):
        rules = []
        for anchor in range(1, 41):
            inherited = [f'else_of("IF@{line}")' for line in range(1, anchor)]
            cond = " and ".join([*inherited, f'F("code") == {anchor}'])
            rules.append(_rule(anchor, cond, [f'ret("RC_{anchor}")']))

        original = example_synthesis._resolved_tree
        with patch.object(
            example_synthesis, "_resolved_tree", wraps=original,
        ) as resolver:
            result = synthesize_examples(rules)

        self.assertTrue(result[40])
        self.assertLessEqual(resolver.call_count, len(rules) * 2)
        self.assertEqual(
            len(result[40][0]["given"]),
            len(set(result[40][0]["given"])),
        )

    def test_syntax_error_condition_yields_no_examples(self):
        got = synthesize_examples([_rule(1, 'F("a" ==', [])])[1]
        self.assertEqual([], got)

    def test_predicate_call_condition_is_kept_as_a_whole(self):
        rules = [_rule(1, 'F("strncmp(x, \\"03\\", n)") == 0', ['ret("RC_NRM")'])]
        got = synthesize_examples(rules)[1]
        self.assertEqual(["F('strncmp(x, \"03\", n)') == 0"], got[0]["given"])


class SynthesisStaysInsideLeafContract(unittest.TestCase):
    """합성물은 개행 없는 Rule truth를 길이로 버리지 않는다."""

    def test_every_synthesized_leaf_fits_the_contract(self):
        cond = 'F("%s") == "01" and F("b") == "02"' % ("x" * 400)
        got = synthesize_examples([_rule(1, cond, ['ret("RC_ERR")'])])[1]
        self.assertTrue(got)
        for example in got:
            for leaf in (*example["given"], *example["then"]):
                self.assertNotIn("\n", leaf)

    def test_long_but_valid_satisfying_leaf_is_not_truncated(self):
        cond = 'F("%s") == "03"' % ("x" * 466)
        got = synthesize_examples([_rule(1, cond, ['ret("RC")'])])[1]
        self.assertEqual(["satisfy"], [example["kind"] for example in got])

    def test_long_parser_owned_predicate_is_not_silently_dropped(self):
        source_predicate = " || ".join(f"code == {index}" for index in range(80))
        cond = f'F("{source_predicate}")'

        got = synthesize_examples([_rule(1, cond, ['ret("RC")'])])[1]

        self.assertEqual(1, len(got))
        self.assertEqual(source_predicate, got[0]["given"][0][3:-2])
        ExampleItem.model_validate(got[0])


if __name__ == "__main__":
    unittest.main()
