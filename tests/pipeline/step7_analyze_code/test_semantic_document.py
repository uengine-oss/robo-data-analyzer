from __future__ import annotations

import unittest

from pipeline.models.code_analysis_output import RuleItem
from pipeline.steps.step7_analyze_code.semantic_document import render_semantic_document


class SemanticDocumentTest(unittest.TestCase):
    def test_exact_literals_conditions_and_effects_are_not_interpreted(self) -> None:
        document = render_semantic_document(
            purpose="요청을 분기하고 결과를 반환한다.",
            signature="process(request)",
            rules=[
                RuleItem(
                    anchor_line=20,
                    cond='F("strncmp(state, \\"C\\", 1)")',
                    then=['set(F("result"), "01")', 'call("next_step")'],
                ),
                RuleItem(anchor_line=10, cond=None, then=['ret("00")']),
            ],
            callees=("next_step",),
            reads=("db:orders",),
            writes=("db:audit",),
            returns=('"00"',),
        )
        self.assertLess(document.index("R10"), document.index("R20"))
        self.assertIn('F("strncmp(state, \\"C\\", 1)")', document)
        self.assertIn('set(F("result"), "01")', document)
        self.assertIn('ret("00")', document)
        self.assertNotIn("유선", document)
        self.assertNotIn("무선", document)
        self.assertNotIn("정상", document)

    def test_same_renderer_accepts_source_owned_opaque_shapes(self) -> None:
        conditions = (
            'F("status == \\"READY\\"")',
            'F("order.state().equals(READY)")',
            "F(\"NVL(flag, 'N') = 'Y'\")",
        )
        for index, condition in enumerate(conditions, 1):
            with self.subTest(condition=condition):
                document = render_semantic_document(
                    purpose="대상의 상태를 확인한다.",
                    signature="target",
                    rules=[RuleItem(anchor_line=index, cond=condition, then=["raise_error()"])]
                )
                self.assertIn(condition, document)

    def test_empty_model_purpose_is_explicit_failure(self) -> None:
        with self.assertRaisesRegex(ValueError, "purpose"):
            render_semantic_document(purpose="", signature="x", rules=[])


if __name__ == "__main__":
    unittest.main()
