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
from pipeline.models.rule_response_schema import AssignedEffect, _render_effect
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
        effect = AssignedEffect(
            op="jump", target="PFM_CATCH", value=None, from_source=None)
        self.assertEqual('jump("PFM_CATCH")', _render_effect(effect))


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
