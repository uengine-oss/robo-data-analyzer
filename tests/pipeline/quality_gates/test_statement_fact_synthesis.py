"""step6 statement fact 합성 검증 (spec 016 E-3c).

지역 선언 초기화(VARIABLE.init_value)는 ASSIGNMENT-동등 사실로 합성되고,
같은 행에 명시 ASSIGNMENT 가 있으면 중복 합성하지 않으며, routine 밖 선언은
합성하지 않는다.
"""
import unittest

from graph.schema.graph_nodes import CodeNode, Variable
from pipeline.models.analysis_context import StatementFact
from pipeline.steps.step6_build_context.control_subtree import build_statement_facts


def _routine(rid: str, start: int, end: int) -> CodeNode:
    return CodeNode(
        id=rid, signature=rid, file_path="a.c",
        start_line=start, end_line=end, label="FUNCTION",
    )


def _statement(sid: str, label: str, line: int, **kw) -> CodeNode:
    return CodeNode(
        id=sid, signature="", file_path="a.c",
        start_line=line, end_line=line, label=label,
        parent_id=kw.pop("parent_id", None), **kw,
    )


def _variable(vid: str, name: str, line: int, init: str, file_path: str = "a.c") -> Variable:
    return Variable(
        id=vid, owner_id="m", name=name, file_path=file_path,
        start_line=line, end_line=line, label="VARIABLE", init_value=init,
    )


class StatementFactSynthesisTest(unittest.TestCase):

    def test_local_declaration_initializer_becomes_assignment_fact(self):
        routine = _routine("f", 10, 30)
        facts = build_statement_facts(
            [routine], [_variable("v1", "rc", 12, "0")],
        )
        self.assertEqual(
            [StatementFact("v1::init", "ASSIGNMENT", 3, 3,
                           expression="0", target="rc", operator="=")],
            facts["f"],
        )

    def test_module_level_declaration_not_synthesized(self):
        routine = _routine("f", 10, 30)
        facts = build_statement_facts(
            [routine], [_variable("v1", "g_flag", 2, "1")],
        )
        self.assertNotIn("f", facts)

    def test_explicit_assignment_on_same_line_wins(self):
        routine = _routine("f", 10, 30)
        explicit = _statement("s", "ASSIGNMENT", 12, parent_id="f",
                              target="self.rc", operator="=", expression="0")
        facts = build_statement_facts(
            [routine, explicit], [_variable("v1", "rc", 12, "0")],
        )
        self.assertEqual(1, len(facts["f"]))
        self.assertEqual("s", facts["f"][0].node_id)

    def test_narrowest_routine_owns_nested_declaration(self):
        outer = _routine("outer", 1, 100)
        inner = _routine("inner", 10, 20)
        facts = build_statement_facts(
            [outer, inner], [_variable("v1", "x", 15, "call()")],
        )
        self.assertIn("inner", facts)
        self.assertNotIn("outer", facts)


if __name__ == "__main__":
    unittest.main()
