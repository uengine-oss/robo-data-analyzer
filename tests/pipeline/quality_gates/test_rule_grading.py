# -*- coding: utf-8 -*-
"""spec 116 4단계 — 행 단위 채점기 반증(오류주입) 테스트.

spec §14 계약: 가짜 행·반전·창작·어휘 밖·축퇴·중복·할당 밖은 전부 검출되고,
else_of 참조·F() 안 함수식은 오탐하지 않는다(하네스 오탐 2종의 회귀 방지).

fixture 는 parser AST 계약(spec 016)을 그대로 제공한다: 조건은
``ControlSubtreeNode.expression``, 종료/반환/대입은 ``StatementFact``,
호출은 정확한 Parser 발생 객체다. analyzer 는 source 를 재탐색하지 않는다.
"""
from __future__ import annotations

import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.models.code_analysis_output import (
    ExampleItem,
    RulesExamplesOutput,
    RuleItem,
)
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import expected_rule_condition
from pipeline.quality_gates.rule_grading import (
    canonical_form,
    conditions_equal,
    grade_rules,
)
from tests.pipeline.parser_call_fixtures import parser_call_occurrences

SOURCE = (
    'if (strncmp(gc_Bank_Cd, "03", LEN_BANK) == 0)\n'    # 1
    "{\n"                                                 # 2
    "    strncpy(gc_Acnt, itf->in.bank_num, LEN_ACNT);\n" # 3
    "}\n"                                                 # 4
    "else\n"                                              # 5
    "{\n"                                                 # 6
    "    rc = RC_ERR;\n"                                  # 7
    "}\n"                                                 # 8
    "return rc;\n"                                        # 9
)
TREE = [
    ControlSubtreeNode(
        node_id="if", ntype="IF", start=1, end=4,
        expression='strncmp(gc_Bank_Cd, "03", LEN_BANK) == 0',
    ),
    ControlSubtreeNode(node_id="el", ntype="ELSE", start=5, end=8),
]
STATEMENTS = (
    StatementFact("a7", "ASSIGNMENT", 7, 7,
                  expression="RC_ERR", target="rc", operator="="),
    StatementFact("r9", "RETURN", 9, 9, expression="rc"),
)
CALL_LINES = ((1, "strncmp"), (3, "strncpy"))


def _inventory():
    return build_inventory(
        SOURCE, TREE, "c", statements=STATEMENTS,
        call_occurrences=parser_call_occurrences(CALL_LINES),
    )


def _rule(anchor, cond, then):
    # examples는 LLM 출력이 아니다 — 결정론 합성(spec 116 §21).
    return RuleItem(
        anchor_line=anchor,
        structural_condition=cond,
        structural_effects=list(then),
    )


def _grade(rules, assigned=None):
    return grade_rules(
        RulesExamplesOutput(rules=rules), _inventory(), SOURCE, assigned=assigned
    )


class ConditionlessLoopEvidence(unittest.TestCase):
    """`for(;;)` 무한 루프 — grammar 에 판정절이 없다(framework 실측:
    zapamcom10200.c 1948행에서 "branch condition source missing" run fatal).

    조건 없는 decision 노드는 경로에 자기 조건 항을 만들지 않는다(창작 금지).
    자기 행과 그 안쪽 행의 조상 경로 둘 다 조건 원문 요구 없이 성립해야 한다.
    """

    SOURCE = (
        "for(;;)\n"            # 1
        "{\n"                  # 2
        "    rc = step();\n"   # 3
        "    if (rc == 0)\n"   # 4
        "    {\n"              # 5
        "        break;\n"     # 6
        "    }\n"              # 7
        "}\n"                  # 8
    )
    TREE = [
        ControlSubtreeNode(
            node_id="loop", ntype="LOOP", start=1, end=8,
            children=[ControlSubtreeNode(
                node_id="if", ntype="IF", start=4, end=7, expression="rc == 0",
            )],
        ),
    ]
    STATEMENTS = (
        StatementFact("a3", "ASSIGNMENT", 3, 3,
                      expression="step()", target="rc", operator="="),
    )

    def _inventory(self):
        return build_inventory(
            self.SOURCE, self.TREE, "c",
            statements=self.STATEMENTS,
            call_occurrences=parser_call_occurrences(((3, "step"),)),
        )

    def test_conditionless_loop_row_has_no_self_condition_term(self):
        from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
        payload = rule_evidence(self._inventory(), (1,))
        self.assertEqual([], payload["rows"]["R1"]["when"])

    def test_inner_decision_path_skips_conditionless_ancestor(self):
        from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
        payload = rule_evidence(self._inventory(), (4,))
        self.assertEqual(["C4"], payload["rows"]["R4"]["when"])
        self.assertEqual("rc == 0", payload["conditions"]["C4"])


class CleanOutputPasses(unittest.TestCase):
    def test_void_return_is_accepted_as_a_direct_targetless_effect(self):
        source = "void f(int bad) {\n  if (bad) return;\n}"
        inventory = build_inventory(
            source,
            (ControlSubtreeNode(
                node_id="if@2", ntype="IF", start=2, end=2, expression="bad",
            ),),
            "c",
            statements=(StatementFact("return@2", "RETURN", 2, 2),),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[
                _rule(2, 'F("bad")', ["ret(None)"]),
            ]),
            inventory,
            source,
            assigned=(2,),
        )
        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])

    def test_correct_rules_have_no_rejection(self):
        report = _grade([
            _rule(1, 'F("strncmp(gc_Bank_Cd, \\"03\\", LEN_BANK)") == 0',
                  ['set(F("gc_Acnt"), F("itf->in.bank_num"))']),
            _rule(5, 'else_of("IF@1")', ['set(F("rc"), "RC_ERR")']),
            # 9행: 분기 밖 [exit] — 종료 계약도 행 의무다 (spec 116 §18-6)
            _rule(9, None, ['ret(F("rc"))']),
        ])
        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])
        # Generic code cannot classify C library-copy calls as assignments.  The
        # structurally valid candidate remains usable while that semantic point is surfaced.
        self.assertTrue(any(verdict.findings for verdict in report.verdicts))
        self.assertEqual([], report.missing)
        self.assertEqual([], report.fabricated_rows)

    def test_else_of_and_f_expression_do_not_false_positive(self):
        # 하네스 오탐 2종 회귀 방지: else_of 인자·F() 안 함수식은 창작이 아니다.
        report = _grade([
            _rule(1, 'F("strncmp(gc_Bank_Cd, \\"03\\", LEN_BANK)") == 0', []),
            _rule(5, 'else_of("IF@1")', []),
        ])
        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])

    def test_handler_entry_without_direct_effect_is_a_valid_rule(self):
        source = "void f(void)\n{\nPFM_CATCH();\n}\n"
        inventory = build_inventory(
            source, [], "c", handler_markers=("PFM_CATCH",),
            statements=(),
            call_occurrences=parser_call_occurrences(((3, "PFM_CATCH"),)),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[
                _rule(3, None, []),
            ]),
            inventory,
            source,
            assigned={3},
            require_exact_conditions=True,
        )
        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])
        self.assertEqual([], report.missing)


class ExactStructuralConditionContract(unittest.TestCase):
    def test_exact_source_path_passes_and_other_grounded_path_is_rejected(self):
        inventory = _inventory()
        exact = expected_rule_condition(inventory, 1)
        other_grounded = expected_rule_condition(inventory, 5)
        effect = ['set(F("gc_Acnt"), F("itf->in.bank_num"))']

        accepted = grade_rules(
            RulesExamplesOutput(rules=[_rule(1, exact, effect)]),
            inventory,
            SOURCE,
            assigned={1},
            require_exact_conditions=True,
        )
        rejected = grade_rules(
            RulesExamplesOutput(rules=[_rule(1, other_grounded, effect)]),
            inventory,
            SOURCE,
            assigned={1},
            require_exact_conditions=True,
        )

        self.assertEqual([], accepted.rejected)
        self.assertEqual([1], [verdict.anchor for verdict in rejected.rejected])
        self.assertTrue(any(
            "정확한 구조 경로" in reason
            for reason in rejected.verdicts[0].reasons
        ))


class InjectedDefectsAreCaught(unittest.TestCase):
    def test_fabricated_anchor_row(self):
        report = _grade([_rule(999, None, ['call("x", None, [])'])])
        self.assertEqual([999], report.fabricated_rows)

    def test_or_to_and_inversion_is_contradiction(self):
        report = _grade([
            _rule(1, 'F("gc_Bank_Cd") == "03" and F("gc_Bank_Cd") == "003"', []),
            _rule(5, 'else_of("IF@1")', []),
        ])
        reasons = report.verdicts[0].reasons
        self.assertTrue(any("모순 AND" in reason for reason in reasons), reasons)

    def test_fabricated_literal_is_caught(self):
        report = _grade([
            _rule(1, 'F("gc_Bank_Cd") == "99"', []),   # "99" 는 소스에 없다
            _rule(5, 'else_of("IF@1")', []),
        ])
        reasons = report.verdicts[0].reasons
        self.assertTrue(any("창작" in reason for reason in reasons), reasons)

    def test_vocabulary_outside_effects_is_rejected(self):
        report = _grade([
            _rule(1, None, ['launch_missile("x")']),
            _rule(5, 'else_of("IF@1")', []),
        ])
        reasons = report.verdicts[0].reasons
        self.assertTrue(any("어휘" in reason for reason in reasons), reasons)

    def test_degenerate_output_yields_missing_by_subtraction(self):
        # 축퇴(1행만) — 뺄셈이 else 행과 최상위 [exit] 행(9)의 누락을 특정한다.
        report = _grade([_rule(1, 'F("strncmp(gc_Bank_Cd, \\"03\\", LEN_BANK)") == 0', [])])
        self.assertEqual([5, 9], report.missing)

    def test_duplicate_anchor_detected(self):
        report = _grade([
            _rule(1, None, []), _rule(1, None, []),
            _rule(5, 'else_of("IF@1")', []),
        ])
        self.assertEqual([1], report.duplicated_rows)

    def test_out_of_assignment_row_detected(self):
        report = _grade(
            [_rule(1, None, []), _rule(5, 'else_of("IF@1")', [])],
            assigned={1},
        )
        self.assertEqual([5], report.out_of_assignment)

    def test_python_syntax_error_is_row_level(self):
        report = _grade([
            _rule(1, 'F("gc_Bank_Cd") ==', []),
            _rule(5, 'else_of("IF@1")', []),
        ])
        self.assertFalse(report.verdicts[0].accepted)
        self.assertTrue(report.verdicts[1].accepted)   # 옆 행은 살아남는다

    def test_else_of_unknown_row_reference(self):
        report = _grade([
            _rule(1, None, []),
            _rule(5, 'else_of("IF@777")', []),
        ])
        reasons = report.verdicts[1].reasons
        self.assertTrue(any("분기표에 없는 행" in reason for reason in reasons), reasons)

    def test_failure_message_names_rows_and_reasons(self):
        report = _grade([_rule(1, 'F("gc_Bank_Cd") == "99"', [])])
        message = report.failure_message()
        self.assertIn("행 1", message)
        self.assertIn("창작", message)
        self.assertIn("행 5", message)   # 누락도 구체 명시


class ThenBranchCalleeComparison(unittest.TestCase):
    """then ↔ 분기표 직접호출 대조 — 거절이 아니라 findings 로 표면화한다."""

    SOURCE = (
        'if (a == "1")\n{\n    x();\n}\n'
        "else\n{\n    y();\n}\n"
    )
    TREE = [
        ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4,
                           expression='a == "1"'),
        ControlSubtreeNode(node_id="el", ntype="ELSE", start=5, end=8),
    ]

    def _grade(self, rules):
        inventory = build_inventory(
            self.SOURCE, self.TREE, "c",
            statements=(),
            call_occurrences=parser_call_occurrences(((3, "x"), (7, "y"))),
        )
        return grade_rules(RulesExamplesOutput(rules=rules), inventory, self.SOURCE)

    def test_call_outside_branch_callees_is_a_finding_not_rejection(self):
        report = self._grade([
            _rule(1, 'F("a") == "1"', ['call("y", None, [])']),
            _rule(5, 'else_of("IF@1")', ['call("y", None, [])']),
        ])
        self.assertTrue(report.verdicts[0].accepted)     # 거절 아님
        self.assertTrue(
            any("직접호출 목록에 없음" in finding
                for finding in report.verdicts[0].findings),
            report.verdicts[0].findings,
        )
        self.assertEqual([], report.verdicts[1].findings)   # 자기 분기 호출은 무결


class DirectEffectOwnership(unittest.TestCase):
    SOURCE = (
        "if (flag)\n{\n    return 1;\n}\n"
        "else\n{\n    rc = 2;\n}\n"
        "return rc;\n"
    )
    TREE = [
        ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4,
                           expression="flag"),
        ControlSubtreeNode(node_id="else", ntype="ELSE", start=5, end=8),
    ]
    STATEMENTS = (
        StatementFact("r3", "RETURN", 3, 3, expression="1"),
        StatementFact("a7", "ASSIGNMENT", 7, 7,
                      expression="2", target="rc", operator="="),
        StatementFact("r9", "RETURN", 9, 9, expression="rc"),
    )

    def _grade(self, rules):
        inventory = build_inventory(
            self.SOURCE, self.TREE, "c", statements=self.STATEMENTS,
        )
        return grade_rules(RulesExamplesOutput(rules=rules), inventory, self.SOURCE)

    def test_return_owned_by_other_row_is_rejected(self):
        report = self._grade([
            _rule(1, 'F("flag")', ["ret(1)"]),
            _rule(5, 'else_of("IF@1")', ['ret(F("rc"))']),
            _rule(9, None, ['ret(F("rc"))']),
        ])
        self.assertTrue(any(
            "직접 source에 없음" in reason for reason in report.verdicts[1].reasons
        ))

    def test_wrong_direct_assignment_value_is_rejected(self):
        report = self._grade([
            _rule(1, 'F("flag")', ["ret(1)"]),
            _rule(5, 'else_of("IF@1")', ['set(F("rc"), 1)']),
            _rule(9, None, ['ret(F("rc"))']),
        ])
        self.assertTrue(any("우변" in reason for reason in report.verdicts[1].reasons))

    def test_exact_direct_effects_pass(self):
        report = self._grade([
            _rule(1, 'F("flag")', ["ret(1)"]),
            _rule(5, 'else_of("IF@1")', ['set(F("rc"), 2)']),
            _rule(9, None, ['ret(F("rc"))']),
        ])
        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])
        self.assertEqual([], report.verdicts[1].findings)

    def test_python_scalar_notation_accepts_equivalent_hex_source_literal(self):
        source = "value = 0x00;\nreturn value;\n"
        inventory = build_inventory(
            source, [], "c",
            statements=(
                StatementFact("a1", "ASSIGNMENT", 1, 1,
                              expression="0x00", target="value", operator="="),
                StatementFact("r2", "RETURN", 2, 2, expression="value"),
            ),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[
                _rule(1, None, ['set(F("value"), 0)']),
                _rule(2, None, ['ret(F("value"))']),
            ]),
            inventory,
            source,
        )

        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])

    def test_entry_assignment_value_is_row_local(self):
        source = "ix = 0;\nreturn ix;\n"
        inventory = build_inventory(
            source, [], "c",
            statements=(
                StatementFact("a1", "ASSIGNMENT", 1, 1,
                              expression="0", target="ix", operator="="),
                StatementFact("r2", "RETURN", 2, 2, expression="ix"),
            ),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[
                _rule(1, None, ['set(F("ix"), 1)']),
                _rule(2, None, ['ret(F("ix"))']),
            ]),
            inventory,
            source,
        )
        self.assertTrue(any("우변" in reason for reason in report.verdicts[0].reasons))


class SqlSampleGrading(unittest.TestCase):
    """DBMS(PL/SQL) 시료 — 게이트 로직은 언어를 모르고 표(lexicon)만 바꿔 같은 채점을 한다."""

    SOURCE = (
        "IF v_type = 'VIP' THEN\n"       # 1
        "    v_rate := 0.8;\n"           # 2
        "ELSE\n"                          # 3
        "    v_rate := 1.0;\n"           # 4
        "END IF;\n"                       # 5
        "RETURN v_total;\n"               # 6
    )
    TREE = [
        ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=2,
                           expression="v_type = 'VIP'"),
        ControlSubtreeNode(node_id="el", ntype="ELSE", start=3, end=5),
    ]
    STATEMENTS = (
        StatementFact("a2", "ASSIGNMENT", 2, 2,
                      expression="0.8", target="v_rate", operator=":="),
        StatementFact("a4", "ASSIGNMENT", 4, 4,
                      expression="1.0", target="v_rate", operator=":="),
        StatementFact("r6", "RETURN", 6, 6, expression="v_total"),
    )

    def _grade(self, rules):
        inventory = build_inventory(
            self.SOURCE, self.TREE, "sql", statements=self.STATEMENTS,
        )
        return grade_rules(
            RulesExamplesOutput(rules=rules), inventory, self.SOURCE
        )

    def test_clean_sql_rules_pass_with_top_level_return_obligation(self):
        report = self._grade([
            _rule(1, "F(\"v_type\") == 'VIP'", ['set(F("v_rate"), 0.8)']),
            _rule(3, 'else_of("IF@1")', ['set(F("v_rate"), 1.0)']),
            _rule(6, None, ['ret(F("v_total"))']),
        ])
        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])
        self.assertEqual([], report.missing)

    def test_fabricated_sql_literal_is_caught(self):
        report = self._grade([
            _rule(1, "F(\"v_type\") == 'PREMIUM'", []),   # 소스에 없는 값
            _rule(3, 'else_of("IF@1")', []),
            _rule(6, None, ['ret(F("v_total"))']),
        ])
        reasons = report.verdicts[0].reasons
        self.assertTrue(any("창작" in reason for reason in reasons), reasons)


class NormalizationEquivalence(unittest.TestCase):
    def test_commutative_or_is_equal(self):
        self.assertTrue(conditions_equal(
            'F("a") == "1" or F("b") == "2"',
            'F("b") == "2" or F("a") == "1"',
        ))

    def test_not_eq_folds_to_noteq(self):
        self.assertTrue(conditions_equal('not (F("a") == "1")', 'F("a") != "1"'))

    def test_double_negation_folds(self):
        self.assertTrue(conditions_equal('not (not (F("a") == "1"))', 'F("a") == "1"'))

    def test_constant_side_is_normalized(self):
        self.assertTrue(conditions_equal('2 == F("x")', 'F("x") == 2'))

    def test_different_meaning_stays_different(self):
        self.assertNotEqual(
            canonical_form('F("a") == "1"'), canonical_form('F("a") != "1"')
        )


class SourceIntrinsicGroundingBoundary(unittest.TestCase):
    """Canonical Python builtins pass only with exact ontology-backed source meaning."""

    SOURCE = (
        "if (strlen(instr) != 13)\n"
        "{\n"
        "    return 0;\n"
        "}\n"
        "return 1;\n"
    )
    TREE = [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4,
                               expression="strlen(instr) != 13")]
    STATEMENTS = (
        StatementFact("r3", "RETURN", 3, 3, expression="0"),
        StatementFact("r5", "RETURN", 5, 5, expression="1"),
    )

    def _grade(self, cond):
        inventory = build_inventory(
            self.SOURCE, self.TREE, "c", statements=self.STATEMENTS,
            call_occurrences=parser_call_occurrences(((1, "strlen"),)),
        )
        return grade_rules(
            RulesExamplesOutput(rules=[
                _rule(1, cond, ["ret(0)"]),
                _rule(5, None, ["ret(1)"]),
            ]),
            inventory,
            self.SOURCE,
        )

    def test_len_is_allowed_when_its_field_is_source_grounded(self):
        report = self._grade('len(F("instr")) != 13')
        self.assertEqual([], report.rejected, [v.reasons for v in report.verdicts])
        self.assertEqual([], report.verdicts[0].findings)

    def test_len_with_different_argument_is_rejected(self):
        report = self._grade('len(F("other")) != 13')
        self.assertFalse(report.verdicts[0].accepted)
        self.assertTrue(
            any("일치하는 source intrinsic" in reason or "SOURCE에 없음" in reason
                for reason in report.verdicts[0].reasons),
            report.verdicts[0].reasons,
        )

    def test_raw_source_operator_equivalence_is_not_claimed(self):
        report = self._grade('len(F("instr")) == 13')
        self.assertTrue(report.verdicts[0].accepted)
        self.assertEqual([], report.verdicts[0].reasons)

    def test_len_source_meaning_is_not_inferred_from_raw_language_syntax(self):
        source = 'if (instr != "")\n{\n    return 0;\n}\nreturn 1;\n'
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4,
                                expression='instr != ""')],
            "c",
            statements=(
                StatementFact("r3", "RETURN", 3, 3, expression="0"),
                StatementFact("r5", "RETURN", 5, 5, expression="1"),
            ),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[
                _rule(1, 'len(F("instr")) != 13', ["ret(0)"]),
                _rule(5, None, ["ret(1)"]),
            ]),
            inventory,
            source,
        )
        self.assertTrue(report.verdicts[0].accepted)
        self.assertEqual([], report.verdicts[0].reasons)

    def test_loop_decision_cannot_hide_condition_as_null(self):
        source = "for (ix = 6; ix < 13; ix++)\n{\n    work();\n}\n"
        # parser LOOP expression 은 판정절만이다 (spec 016) — 머리 대입은 statement 아님.
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(node_id="loop", ntype="LOOP", start=1, end=4,
                                expression="ix < 13")],
            "c",
            statements=(),
            call_occurrences=parser_call_occurrences(((3, "work"),)),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[_rule(1, None, ['call("work", None, [])'])]),
            inventory,
            source,
        )
        self.assertTrue(any("cond가 null" in reason for reason in report.verdicts[0].reasons))

    def test_post_test_loop_body_has_no_precondition(self):
        source = "do {\n    work();\n} while (again);\n"
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(
                node_id="do", ntype="LOOP", start=1, end=3,
                expression="again", condition_timing="post",
            )],
            "c",
            statements=(),
            call_occurrences=parser_call_occurrences(((2, "work"),)),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[_rule(1, None, ['call("work", None, [])'])]),
            inventory,
            source,
        )
        self.assertTrue(report.verdicts[0].accepted, report.verdicts[0].reasons)

    def test_entry_effect_cannot_be_empty(self):
        source = "ix = 0;\nreturn ix;\n"
        inventory = build_inventory(
            source, [], "c",
            statements=(
                StatementFact("a1", "ASSIGNMENT", 1, 1,
                              expression="0", target="ix", operator="="),
                StatementFact("r2", "RETURN", 2, 2, expression="ix"),
            ),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[
                _rule(1, None, []),
                _rule(2, None, ["ret(F(\"ix\"))"]),
            ]),
            inventory,
            source,
        )
        self.assertTrue(any("then이 비어" in reason for reason in report.verdicts[0].reasons))


class PlainConditionGroundingBoundary(unittest.TestCase):
    def test_identifier_equal_to_same_spelled_string_is_not_rewritten_inside_literal(self):
        source = 'if (x == "x")\n{\n    return 1;\n}\nreturn 0;\n'
        inventory = build_inventory(
            source,
            [ControlSubtreeNode(node_id="if", ntype="IF", start=1, end=4,
                                expression='x == "x"')],
            "c",
            statements=(
                StatementFact("r3", "RETURN", 3, 3, expression="1"),
                StatementFact("r5", "RETURN", 5, 5, expression="0"),
            ),
        )
        report = grade_rules(
            RulesExamplesOutput(rules=[
                _rule(1, 'F("x") == "x"', ["ret(1)"]),
                _rule(5, None, ["ret(0)"]),
            ]),
            inventory,
            source,
        )
        self.assertEqual([], report.verdicts[0].reasons)
        self.assertEqual([], report.verdicts[0].findings)
