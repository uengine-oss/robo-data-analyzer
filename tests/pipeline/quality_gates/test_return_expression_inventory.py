# -*- coding: utf-8 -*-
"""callee 반환 힌트는 상태 추적이 아니라 parser AST 가 보존한 return 표현식이다.

spec 016 전환: 반환식의 생산자는 언어별 listener(RETURN.expression)이고 analyzer 는
소비만 한다. legacy 한 줄 스캐너가 버리던 multiline 표현식도 AST 는 보존한다.
"""
from __future__ import annotations

import unittest

from pipeline.models.analysis_context import StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory


class ReturnExpressionInventory(unittest.TestCase):
    def test_supported_languages_consume_ast_return_facts(self):
        cases = (
            ("c", "return RC_ERR;", "RC_ERR"),
            ("java", "return result.getCode();", "result.getCode()"),
            ("python", "return result", "result"),
            ("sql", "RETURN v_result;", "v_result"),
        )
        for lang, source, expression in cases:
            with self.subTest(lang=lang):
                inventory = build_inventory(
                    source, (), lang,
                    statements=(StatementFact("r1", "RETURN", 1, 1,
                                              expression=expression),),
                )
                self.assertEqual(
                    frozenset({expression}), inventory.return_expressions,
                )

    def test_multiline_expression_is_preserved(self):
        # legacy 한 줄 스캐너는 이 표현식을 잃었다 — AST 계약의 수리 지점(07 §2).
        inventory = build_inventory(
            "return (\n  current_value\n);\nreturn FINAL;",
            (),
            "c",
            statements=(
                StatementFact("r1", "RETURN", 1, 3, expression="(\n  current_value\n)"),
                StatementFact("r4", "RETURN", 4, 4, expression="FINAL"),
            ),
        )
        self.assertEqual(
            frozenset({"( current_value )", "FINAL"}), inventory.return_expressions,
        )

    def test_return_without_value_contributes_no_expression_but_is_exit(self):
        inventory = build_inventory(
            "return;", (), "c",
            statements=(StatementFact("r1", "RETURN", 1, 1),),
        )
        self.assertEqual(frozenset(), inventory.return_expressions)
        self.assertEqual(
            [(1, "exit")],
            [(line, kind) for line, kind, _ in inventory.termination_rows],
        )


if __name__ == "__main__":
    unittest.main()
