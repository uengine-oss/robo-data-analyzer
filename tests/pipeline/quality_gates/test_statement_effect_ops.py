"""statement 효과 편입 계약 (goto → jump) — 온톨로지 데이터 주도.

framework 실측(2026-08-05): `if (rc != 0) goto PFM_CATCH;` 분기는 점프가 유일한
효과인데 effects 보기가 비어 모델이 고를 사실이 없었다. 계약:

- ontology `statement_effect_ops` 가 statement 라벨 → Rule 효과 op 를 데이터로 선언한다
  (새 구문 편입 = 데이터 1줄 — DRY, 코드에 라벨 하드코딩 금지).
- 선언된 statement 는 소유 행의 직접 효과 보기로 노출된다(op=선언값, target=대상).
- 어휘 단일 정의처(rule_expression.EFFECT_FUNCTIONS)와 드리프트가 없어야 한다.
"""
import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.languages import statement_effect_ops
from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.models.rule_expression import (
    EFFECT_FUNCTIONS,
    LeafVocabularyError,
    parse_effect,
)
from pipeline.models.rule_materialization import (
    StructuralEffect,
    render_structural_effect,
)
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.steps.step6_build_context.control_subtree import build_statement_facts


class OntologyEffectOpsTest(unittest.TestCase):
    def test_goto_declares_jump_effect(self):
        self.assertEqual("jump", statement_effect_ops().get("GOTO"))

    def test_ops_stay_inside_effect_vocabulary(self):
        # 어휘 드리프트 감시 — ontology 선언은 닫힌 효과 어휘의 부분집합이어야 한다.
        for op in statement_effect_ops().values():
            self.assertIn(op, EFFECT_FUNCTIONS)


class JumpVocabularyTest(unittest.TestCase):
    def test_jump_effect_parses(self):
        node = parse_effect('jump("PFM_CATCH")')
        self.assertEqual("jump", node.func.id)

    def test_jump_rejects_f_reference_argument(self):
        with self.assertRaises(LeafVocabularyError):
            parse_effect('jump(F("PFM_CATCH"))')

    def test_jump_renders_canonically(self):
        effect = StructuralEffect(
            op="jump", target="PFM_CATCH", value=None, from_source=None)
        self.assertEqual('jump("PFM_CATCH")', render_structural_effect(effect))


class TransactionEffectTest(unittest.TestCase):
    def test_transaction_effects_are_targetless_closed_vocabulary(self):
        self.assertEqual("commit", statement_effect_ops().get("COMMIT"))
        self.assertEqual("rollback", statement_effect_ops().get("ROLLBACK"))
        self.assertEqual("commit", parse_effect("commit()").func.id)
        self.assertEqual("rollback", parse_effect("rollback()").func.id)
        with self.assertRaises(LeafVocabularyError):
            parse_effect('rollback("transaction")')
        self.assertEqual(
            "rollback()",
            render_structural_effect(StructuralEffect(
                op="rollback", target=None, value=None, from_source=None,
            )),
        )

    def test_exception_row_keeps_rollback_as_direct_effect(self):
        source = "\n".join([
            "BEGIN",                       # 1
            "  UPDATE T SET V = 1;",       # 2
            "EXCEPTION",                   # 3
            "  WHEN OTHERS THEN",          # 4
            "    DBMS_OUTPUT.PUT_LINE('x');",  # 5
            "    ROLLBACK;",               # 6
            "END;",                        # 7
        ])
        tree = [ControlSubtreeNode(
            node_id="exception@3", ntype="EXCEPTION", start=3, end=6,
        )]
        inventory = build_inventory(
            source, tree, "sql",
            statements=(StatementFact("rb6", "ROLLBACK", 6, 6),),
            call_lines=((5, "dbms_output.put_line"),),
        )
        payload = rule_evidence(inventory, (3,))
        direct = payload["rows"]["R3"].get("direct") or []
        rendered = [
            render_structural_effect(StructuralEffect.model_validate(payload["effects"][ref]))
            for ref in direct
        ]
        self.assertIn("rollback()", rendered, payload)


class LoopExitEffectTest(unittest.TestCase):
    def test_exit_when_row_keeps_targetless_loop_exit_effect(self):
        self.assertEqual("exit_loop", statement_effect_ops().get("EXIT"))
        self.assertEqual("exit_loop", parse_effect("exit_loop()").func.id)
        source = "LOOP\n  EXIT WHEN C_DATA%NOTFOUND;\nEND LOOP;"
        tree = [ControlSubtreeNode(
            node_id="exit@2", ntype="EXIT", start=2, end=2,
            expression="C_DATA%NOTFOUND",
        )]
        inventory = build_inventory(
            source, tree, "sql",
            statements=(StatementFact("exit@2", "EXIT", 2, 2),),
        )
        payload = rule_evidence(inventory, (2,))
        direct = payload["rows"]["R2"].get("direct") or []
        rendered = [
            render_structural_effect(StructuralEffect.model_validate(payload["effects"][ref]))
            for ref in direct
        ]
        self.assertEqual(["exit_loop()"], rendered)


class FunctionExitEffectTest(unittest.TestCase):
    def test_valued_return_is_not_duplicated_as_targetless_ret(self):
        inventory = build_inventory(
            "int f(void) {\n  return RET_OK;\n}",
            (),
            "c",
            statements=(StatementFact(
                "return@2", "RETURN", 2, 2, expression="RET_OK",
            ),),
        )
        payload = rule_evidence(inventory, (2,))
        direct = payload["rows"]["R2"].get("direct") or []
        rendered = [
            render_structural_effect(StructuralEffect.model_validate(payload["effects"][ref]))
            for ref in direct
        ]
        self.assertEqual(['ret(F("RET_OK"))'], rendered)

    def test_void_return_is_a_targetless_ret_effect(self):
        self.assertEqual("ret", statement_effect_ops().get("RETURN"))
        inventory = build_inventory(
            "void f(int bad) {\n  if (bad) return;\n}",
            (ControlSubtreeNode(
                node_id="if@2", ntype="IF", start=2, end=2, expression="bad",
            ),),
            "c",
            statements=(StatementFact("return@2", "RETURN", 2, 2),),
        )
        payload = rule_evidence(inventory, (2,))
        direct = payload["rows"]["R2"].get("direct") or []
        rendered = [
            render_structural_effect(StructuralEffect.model_validate(payload["effects"][ref]))
            for ref in direct
        ]
        self.assertEqual(["ret(None)"], rendered)
        self.assertEqual("ret", parse_effect("ret(None)").func.id)

    def test_throw_and_raise_are_not_misreported_as_return_values(self):
        self.assertEqual("raise_error", statement_effect_ops().get("THROW"))
        self.assertEqual("raise_error", statement_effect_ops().get("RAISE"))
        inventory = build_inventory(
            "void f(int bad) {\n  if (bad) throw Failure();\n}",
            (ControlSubtreeNode(
                node_id="if@2", ntype="IF", start=2, end=2, expression="bad",
            ),),
            "java",
            statements=(StatementFact(
                "throw@2", "THROW", 2, 2, expression="new Failure()",
            ),),
        )
        payload = rule_evidence(inventory, (2,))
        direct = payload["rows"]["R2"].get("direct") or []
        rendered = [
            render_structural_effect(StructuralEffect.model_validate(payload["effects"][ref]))
            for ref in direct
        ]
        self.assertEqual(["raise_error()"], rendered)
        self.assertNotIn("new Failure()", inventory.return_expressions)


class JumpEvidenceTest(unittest.TestCase):
    SOURCE = "\n".join([
        "long f(long rc) {",           # 1
        "  if (rc != RC_NRM) {",       # 2
        "    goto PFM_CATCH;",         # 3
        "  }",                         # 4
        "  return RC_NRM;",            # 5
        "PFM_CATCH:",                  # 6
        "  return RC_ERR;",            # 7
        "}",                           # 8
    ])
    TREE = [ControlSubtreeNode(
        node_id="if@2", ntype="IF", start=2, end=4, expression="rc != RC_NRM")]
    STATEMENTS = (
        StatementFact("g3", "GOTO", 3, 3, target="PFM_CATCH"),
        StatementFact("r5", "RETURN", 5, 5, expression="RC_NRM"),
        StatementFact("l6", "LABEL", 6, 6, target="PFM_CATCH"),
        StatementFact("r7", "RETURN", 7, 7, expression="RC_ERR"),
    )

    def test_goto_only_branch_gets_jump_effect(self):
        inventory = build_inventory(
            self.SOURCE, self.TREE, "c", handler_markers=("PFM_CATCH",),
            statements=self.STATEMENTS, call_lines=(),
        )
        payload = rule_evidence(inventory, (2,))
        direct = payload["rows"]["R2"].get("direct") or []
        jump_refs = [
            ref for ref in direct
            if payload["effects"][ref]["op"] == "jump"
            and payload["effects"][ref]["target"] == "PFM_CATCH"
        ]
        self.assertEqual(1, len(jump_refs), payload)


class GotoTargetNormalizationTest(unittest.TestCase):
    def test_build_statement_facts_uses_name_as_target_for_goto(self):
        routine = CodeNode(
            id="f", signature="f", file_path="a.c",
            start_line=10, end_line=20, label="FUNCTION",
        )
        goto = CodeNode(
            id="g", name="PFM_CATCH", signature="", file_path="a.c",
            start_line=12, end_line=12, label="GOTO", parent_id="f",
        )
        facts = build_statement_facts([routine, goto])
        self.assertEqual("PFM_CATCH", facts["f"][0].target)


if __name__ == "__main__":
    unittest.main()
