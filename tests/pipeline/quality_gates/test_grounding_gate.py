"""spec 101 근거성 게이트 — 결정론 판정 계약.

실패 계약을 함께 단언한다: 잘린 조건식·주석 오인·동일 효과 라벨 묶음은 **위반이 아니다**.
"""
from __future__ import annotations

import unittest

from pipeline.models.analysis_context import ControlSubtreeNode
from pipeline.models.code_analysis_output import ExampleItem, RuleItem, RulesExamplesOutput
from pipeline.quality_gates.branch_inventory import build_inventory, condition_text
from pipeline.quality_gates.grounding import check_grounding, code_values
from pipeline.quality_gates.lexing import literal_values, split_source


def _rules(*statements: str) -> RulesExamplesOutput:
    return RulesExamplesOutput(
        rules=[RuleItem(statement=s, examples=[ExampleItem(given=s, when="", then="")])
               for s in statements],
        questions=[],
    )


SWITCH_BODY = """
switch (code)
{
case 11:
    handle_apply(ctx);
    break;
case 14:
    handle_apply(ctx);
    break;
case 21:
    handle_cancel(ctx);
    break;
}
""".strip("\n")

SWITCH_TREE = [
    ControlSubtreeNode(node_id="sw", ntype="SWITCH", start=1, end=13, children=[
        ControlSubtreeNode(node_id="c11", ntype="CASE", start=3, end=5),
        ControlSubtreeNode(node_id="c14", ntype="CASE", start=6, end=8),
        ControlSubtreeNode(node_id="c21", ntype="CASE", start=9, end=11),
    ]),
]


class LexingContract(unittest.TestCase):
    def test_comment_split_preserves_line_alignment(self):
        split = split_source("a = 1;\n// b = 2;\nc = 3;", "c")
        self.assertEqual(len(split.live_lines), 3)
        self.assertNotIn("b", split.live(2, 2))
        self.assertIn("b", split.dead(2, 2))

    def test_string_literal_is_not_mistaken_for_a_comment(self):
        split = split_source('url = "http://x";', "c")
        self.assertIn("http://x", split.live())

    def test_inactive_block_is_dead_by_language_table(self):
        split = split_source("#if 0\ndead_call();\n#endif\nlive_call();", "c")
        self.assertNotIn("dead_call", split.live())
        self.assertIn("dead_call", split.dead())
        self.assertIn("live_call", split.live())

    def test_literal_values_exclude_identifiers(self):
        self.assertEqual(literal_values('x == "01"', "c"), {"01"})


class ConditionExtraction(unittest.TestCase):
    def test_multiline_condition_is_not_cut_by_line_count(self):
        body = 'if (eq(a,"11") ||\n    eq(a,"12") ||\n    eq(a,"13"))\n{\n    run();\n}'
        self.assertEqual(literal_values(condition_text(body, "IF"), "c"), {"11", "12", "13"})

    def test_case_condition_stops_at_label_colon(self):
        self.assertEqual(condition_text("case 21:\n    run();", "CASE").strip(), "case 21")


class ValueSetCheck(unittest.TestCase):
    def setUp(self):
        self.inventory = build_inventory(SWITCH_BODY, SWITCH_TREE, "c")

    def test_same_effect_labels_grouped_together_is_not_a_violation(self):
        findings = check_grounding(_rules("코드 '11' 또는 '14' 이면 신청 처리한다"), self.inventory)
        self.assertEqual(findings, [])

    def test_labels_with_different_effects_merged_into_one_item_is_flagged(self):
        findings = check_grounding(_rules("코드 '11' 또는 '21' 이면 처리한다"), self.inventory)
        self.assertTrue(any(f.check == "value_set" for f in findings))
        self.assertIn("11", findings[0].values)

    def test_fabricated_code_value_is_flagged(self):
        body = 'if (eq(a,"11") || eq(a,"13") || eq(a,"21"))\n{\n    run();\n}'
        tree = [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4)]
        findings = check_grounding(
            _rules("코드가 '11','13','21','12' 중 하나이면 갱신한다"),
            build_inventory(body, tree, "c"),
        )
        self.assertTrue(findings)
        self.assertEqual(findings[0].values, ("12",))

    def test_single_code_value_is_never_enough_to_judge(self):
        self.assertEqual(check_grounding(_rules("코드가 '11' 이면 처리"), self.inventory), [])


class DelegationCheck(unittest.TestCase):
    def test_branchless_caller_with_rules_is_flagged(self):
        inventory = build_inventory("rc = child_a(ctx);\nreturn rc;", [], "c")
        findings = check_grounding(_rules("업무 구분에 따라 하위 모듈에 위임한다"), inventory)
        self.assertEqual([f.check for f in findings], ["delegation"])

    def test_branchless_and_callless_body_is_not_flagged(self):
        inventory = build_inventory("x = 1;\nreturn x;", [], "c")
        self.assertEqual(check_grounding(_rules("무언가 한다"), inventory), [])

    def test_empty_rules_produce_no_findings(self):
        inventory = build_inventory("rc = child_a(ctx);", [], "c")
        self.assertEqual(check_grounding(RulesExamplesOutput(), inventory), [])


class LanguageIndependence(unittest.TestCase):
    """게이트 로직에 언어 분기가 없다 — 같은 코드가 표(데이터)만 바꿔 java/python/sql 을 판정한다.

    C 코퍼스로만 실측했으므로 **적중률은 다른 언어에서 미검증**이다. 여기서 단언하는 것은
    "판정 경로가 언어에 걸려 죽지 않는다"까지다.
    """

    JAVA = 'if (code.equals("11") || code.equals("14")) {\n    apply();\n}'
    PYTHON = 'if code == "11" or code == "14":\n    apply()'
    SQL = "IF v_code = '11' OR v_code = '14' THEN\n    apply();\nEND IF;"

    def test_condition_literals_are_read_in_every_language(self):
        for lang, body in (("java", self.JAVA), ("python", self.PYTHON), ("sql", self.SQL)):
            with self.subTest(lang=lang):
                tree = [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=3)]
                inventory = build_inventory(body, tree, lang)
                self.assertEqual(inventory.all_branches[0].cond_values, frozenset({"11", "14"}))

    def test_line_comment_token_comes_from_the_table(self):
        self.assertNotIn("dead", split_source("live();\n// dead();", "java").live())
        self.assertNotIn("dead", split_source("live()\n# dead()", "python").live())
        self.assertNotIn("dead", split_source("live();\n-- dead();", "sql").live())

    def test_fabricated_value_is_flagged_in_java_and_python(self):
        for lang, body in (("java", self.JAVA), ("python", self.PYTHON)):
            with self.subTest(lang=lang):
                tree = [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=3)]
                findings = check_grounding(
                    _rules("코드가 '11','14','99' 중 하나이면 처리한다"),
                    build_inventory(body, tree, lang),
                )
                self.assertTrue(findings, f"{lang} 에서 판정이 안 났다")
                self.assertEqual(findings[0].values, ("99",))

    def test_unregistered_language_fails_loudly_instead_of_passing_silently(self):
        from pipeline.languages import UnknownSourceLexiconError

        with self.assertRaises(UnknownSourceLexiconError):
            split_source("x = 1", "cobol")


class OutputValueExtraction(unittest.TestCase):
    def test_unquoted_numbers_need_the_run_s_own_anchor(self):
        self.assertEqual(code_values("과목코드가 101 이면"), set())
        self.assertEqual(code_values("과목코드가 101 이면", frozenset({"101"})), {"101"})

    def test_korean_prose_inside_quotes_is_not_a_code_value(self):
        self.assertEqual(code_values("'간편결제' 이면"), set())


if __name__ == "__main__":
    unittest.main()
