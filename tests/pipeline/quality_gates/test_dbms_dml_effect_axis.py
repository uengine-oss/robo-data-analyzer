"""DBMS DML 효과 축 — 규칙의 `then` 이 비어 있지 않게 한다 (spec 119).

step5 링커가 DML 노드마다 이미 만든 ``(테이블, dml_type)`` 을 rule 증거까지 잇는다.
새 판정 기계가 아니라 좌표 전달이며, framework 는 ``table_ops`` 를 제공하지 않으므로
증거가 한 글자도 달라지지 않는다.

실측 근거(spec 119 §1): rwis 의무 행 59개 중 효과 증거 보유 19개(32%) — framework 는 83%.
"""
import unittest

from pipeline.models.analysis_context import ControlSubtreeNode, StatementFact
from pipeline.quality_gates.branch_inventory import build_inventory
from pipeline.quality_gates.rule_prompt_evidence import rule_evidence
from pipeline.steps.step7_analyze_code.rule_row_batching import required_rule_rows

_SOURCE = "\n".join([
    "PROCEDURE p(v NUMBER) IS",            # 1
    "BEGIN",                               # 2
    "  INSERT INTO LOG_TB VALUES (v);",    # 3
    "  IF v > 0 THEN",                     # 4
    "    UPDATE ACC_TB SET n = v;",        # 5
    "    SELECT c INTO x FROM REF_TB;",    # 6
    "  END IF;",                           # 7
    "END;",                                # 8
])

_TREE = [ControlSubtreeNode(node_id="if@4", ntype="IF", start=4, end=7, expression="v > 0")]


def _evidence(table_ops, tree=_TREE, statements=()):
    inventory = build_inventory(
        _SOURCE, tree, "sql",
        statements=tuple(statements), table_ops=tuple(table_ops),
    )
    return inventory, rule_evidence(inventory, tuple(required_rule_rows(inventory)))


def _ops(payload, key):
    effects = payload.get("effects") or {}
    return sorted(
        (effects[ref]["op"], effects[ref]["target"])
        for ref in payload["rows"][key].get("direct") or ()
    )


class DmlEffectOwnershipTest(unittest.TestCase):

    def test_s1_dml_inside_a_branch_belongs_to_that_branch_row(self):
        _, payload = _evidence([(3, "insert", "LOG_TB"), (5, "update", "ACC_TB")])
        self.assertEqual([("update", "ACC_TB")], _ops(payload, "R4"))

    def test_s2_top_level_dml_creates_and_owns_the_entry_row(self):
        inventory, payload = _evidence([(3, "insert", "LOG_TB"), (5, "update", "ACC_TB")])
        self.assertEqual(3, inventory.entry_line)
        self.assertIn("R3", payload["rows"])
        self.assertEqual("entry", payload["rows"]["R3"]["role"])
        self.assertEqual([("insert", "LOG_TB")], _ops(payload, "R3"))

    def test_s3_two_tables_on_one_line_are_two_options_without_duplication(self):
        _, payload = _evidence([
            (5, "update", "ACC_TB"), (5, "select", "REF_TB"), (5, "update", "ACC_TB"),
        ])
        self.assertEqual(
            [("select", "REF_TB"), ("update", "ACC_TB")], _ops(payload, "R4"),
        )

    def test_s4_child_range_dml_does_not_leak_into_the_parent_row(self):
        tree = [ControlSubtreeNode(
            node_id="if@4", ntype="IF", start=4, end=7, expression="v > 0",
            children=[ControlSubtreeNode(
                node_id="if@5", ntype="IF", start=5, end=6, expression="v > 1")],
        )]
        _, payload = _evidence([(5, "update", "ACC_TB"), (6, "select", "REF_TB")], tree)
        self.assertEqual([], _ops(payload, "R4"))
        self.assertIs(
            True, payload["rows"]["R4"].get("nested_control"),
            "부모 direct에 자식 효과를 복사하지 않되 중첩 로직 존재는 사람용 NL에 알려야 한다",
        )
        self.assertEqual(
            [("select", "REF_TB"), ("update", "ACC_TB")], _ops(payload, "R5"),
        )
        self.assertNotIn("nested_control", payload["rows"]["R5"])

    def test_s5_without_table_ops_the_evidence_is_unchanged(self):
        """framework 경로 — ``table_ops`` 미제공이면 증거가 완전히 같다."""
        statements = (
            StatementFact("s5", "ASSIGNMENT", 5, 5, expression="v", target="n", operator=":="),
        )
        _, without = _evidence((), statements=statements)
        _, also_without = _evidence([], statements=statements)
        self.assertEqual(without, also_without)
        # DML 좌표가 없으면 효과는 대입뿐이고 최상위 DML 이 없으니 entry 행도 없다.
        self.assertEqual([("set", "n")], _ops(without, "R4"))
        self.assertNotIn("R3", without["rows"])


class UndeclaredDmlTypeFailsLoudlyTest(unittest.TestCase):
    """S6 — 미선언 ``dml_type`` 은 조용히 버리지 않고 좌표와 함께 실패한다 (헌법 IV)."""

    def test_s6_unknown_dml_type_raises_with_coordinates(self):
        from graph.schema.graph_nodes import CodeNode
        from pipeline.steps.step6_build_context.build_context_step import _table_op_lines

        class _Edge:
            source_id = "code:p.sql:p@1:UPSERTX@3"
            target_id = "T"
            relation = "WRITES"
            dml_type = "UPSERTX"

        nodes = [
            CodeNode(id="code:p.sql:p@1", signature="p", file_path="p.sql", start_line=1,
                     end_line=8, label="PROCEDURE", name="p", code_text="", owner_id="p.sql",
                     parent_id=None),
            CodeNode(id="code:p.sql:p@1:UPSERTX@3", signature="x", file_path="p.sql",
                     start_line=3, end_line=3, label="MERGE", name="", code_text="",
                     owner_id="p.sql", parent_id="code:p.sql:p@1"),
        ]
        with self.assertRaisesRegex(ValueError, r"미선언 dml_type 'UPSERTX'.*p\.sql:3"):
            _table_op_lines([_Edge()], nodes)


if __name__ == "__main__":
    unittest.main()
