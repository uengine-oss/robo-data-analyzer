"""값 폴딩의 안전 계약 (spec 081).

이름 해석은 step2 의 REFERENCES/INIT_BY 가 하고, 이 모듈은 **값 계산**만 책임진다.
따라서 여기 테스트도 그 엣지를 입력으로 준다.
"""
from __future__ import annotations

import unittest

from graph.schema.graph_edges import InitBy, References
from graph.schema.graph_nodes import Constant, Variable
from pipeline.steps.step5_linking.link_external.symbol_values import SymbolTable

OWNER = "code:pkg:Constants"
ROUTINE = "code:pkg:Service:run"


def _constant(name: str, value: str, owner: str = OWNER) -> Constant:
    return Constant(
        id=f"{owner}:{name}", owner_id=owner, name=name, file_path="Constants.java",
        start_line=1, end_line=1, label="CONSTANT_FIELD", value=value,
    )


class ValueFoldingTest(unittest.TestCase):
    def test_pure_literal_constant_folds(self) -> None:
        constant = _constant("SQL_KEY", '"shop.Order.find"')
        table = SymbolTable([], [constant], [References(ROUTINE, constant.id, 10)], [])
        self.assertEqual(constant.id, table.node_for(ROUTINE, "SQL_KEY"))
        self.assertEqual("shop.Order.find", table.value_of_node(constant.id))

    def test_all_constant_concatenation_folds_through_init_by(self) -> None:
        prefix = _constant("PREFIX", '"java:comp/env/ejb/"')
        name = _constant("NAME", 'PREFIX + "OrderSession"')
        table = SymbolTable(
            [], [prefix, name],
            [References(ROUTINE, name.id, 12)],
            [InitBy(from_id=name.id, source_id=prefix.id)],
        )
        self.assertEqual("java:comp/env/ejb/OrderSession", table.value_of_node(name.id))

    def test_concatenation_without_a_known_origin_stays_unresolved(self) -> None:
        # INIT_BY 가 출처를 지목하지 않으면 피연산자를 확정할 수 없다 → 미해결.
        name = _constant("NAME", 'PREFIX + "OrderSession"')
        table = SymbolTable([], [name], [References(ROUTINE, name.id, 12)], [])
        self.assertIsNone(table.value_of_node(name.id))

    def test_runtime_expression_is_never_folded(self) -> None:
        constant = _constant("RUNTIME", 'System.getProperty("x")')
        table = SymbolTable([], [constant], [References(ROUTINE, constant.id, 3)], [])
        self.assertIsNone(table.value_of_node(constant.id))

    def test_mutable_variable_is_not_a_value_source(self) -> None:
        variable = Variable(
            id="v", owner_id=OWNER, name="KEY", file_path="Svc.java", start_line=1, end_line=1,
            label="VARIABLE", init_value='"shop.key"',
        )
        table = SymbolTable([variable], [], [References(ROUTINE, "v", 5)], [])
        self.assertIsNone(table.value_of_node("v"))

    def test_final_variable_is_a_value_source(self) -> None:
        variable = Variable(
            id="v", owner_id=OWNER, name="KEY", file_path="Svc.java", start_line=1, end_line=1,
            label="FINAL_VARIABLE", init_value="'shop.title'",
        )
        table = SymbolTable([variable], [], [References(ROUTINE, "v", 5)], [])
        self.assertEqual("shop.title", table.value_of_node("v"))

    def test_unreferenced_name_is_not_resolvable(self) -> None:
        constant = _constant("SQL_KEY", '"shop.Order.find"')
        table = SymbolTable([], [constant], [], [])
        self.assertIsNone(table.node_for(ROUTINE, "SQL_KEY"))

    def test_self_referencing_constant_terminates(self) -> None:
        loop = _constant("LOOP", "LOOP")
        table = SymbolTable([], [loop], [References(ROUTINE, loop.id, 1)],
                            [InitBy(from_id=loop.id, source_id=loop.id)])
        self.assertIsNone(table.value_of_node(loop.id))


class LocalLiteralTest(unittest.TestCase):
    def test_java_local_declaration(self) -> None:
        body = 'void a() { final String k = "paymentGateway"; use(k); }'
        self.assertEqual({"k": "paymentGateway"}, SymbolTable.local_literal_values(body))

    def test_plsql_local_declaration(self) -> None:
        body = "  c_name CONSTANT VARCHAR2(60) := 'send_layout.txt';\n"
        self.assertEqual({"c_name": "send_layout.txt"}, SymbolTable.local_literal_values(body))

    def test_reassigned_local_is_dropped(self) -> None:
        body = 'String k = "a"; k = compute(); use(k);'
        self.assertEqual({}, SymbolTable.local_literal_values(body))


if __name__ == "__main__":
    unittest.main()
