from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode, Constant, Variable
from pipeline.steps.step5_linking.link_external.const_propagation import immutable_symbols, referenced_immutable_keys


def _code(text: str) -> CodeNode:
    return CodeNode(
        id="code:pkg:Service:run", signature="run()", file_path="Service.java",
        start_line=10, end_line=20, label="METHOD", name="run", code_text=text,
        owner_id="code:pkg:Service",
    )


class ConstantPropagationTest(unittest.TestCase):
    def test_constant_and_explicit_final_variable_propagate_once(self) -> None:
        constants = [Constant(
            id="code:pkg:Service:SQL_KEY", owner_id="code:pkg:Service", name="SQL_KEY",
            file_path="Service.java", start_line=2, end_line=2, label="CONSTANT_FIELD",
            value='"shop.Order.find"',
        )]
        variables = [Variable(
            id="code:pkg:Service:CFG", owner_id="code:pkg:Service", name="CFG",
            file_path="Service.java", start_line=3, end_line=3, label="FINAL_VARIABLE",
            init_value="'shop.title'",
        )]
        symbols = immutable_symbols(variables, constants, {"shop.Order.find", "shop.title"})
        self.assertEqual(
            [("shop.Order.find", "SQL_KEY", 10), ("shop.title", "CFG", 11)],
            referenced_immutable_keys(_code("use(SQL_KEY);\nread(CFG);"), symbols),
        )

    def test_mutable_impure_duplicate_reassigned_and_string_mentions_are_rejected(self) -> None:
        variables = [Variable(
            id="v", owner_id="code:pkg:Service", name="KEY", file_path="Service.java",
            start_line=1, end_line=1, label="VARIABLE", init_value='"shop.key"',
        )]
        constants = [
            Constant("c1", "code:pkg:Service", "DUP", "Service.java", 1, 1, "CONSTANT_FIELD", '"shop.key"'),
            Constant("c2", "code:pkg:Service", "DUP", "Service.java", 2, 2, "CONSTANT_FIELD", '"shop.key"'),
            Constant("c3", "code:pkg:Service", "COMPOSED", "Service.java", 3, 3, "CONSTANT_FIELD", '"shop." + suffix'),
            Constant("c4", "code:pkg:Service", "SAFE", "Service.java", 4, 4, "CONSTANT_FIELD", '"shop.key"'),
        ]
        symbols = immutable_symbols(variables, constants, {"shop.key"})
        self.assertNotIn("KEY", symbols.get("code:pkg:Service", {}))
        self.assertNotIn("DUP", symbols.get("code:pkg:Service", {}))
        self.assertNotIn("COMPOSED", symbols.get("code:pkg:Service", {}))
        self.assertEqual([], referenced_immutable_keys(_code('log("SAFE"); SAFE = other; use(SAFE);'), symbols))


if __name__ == "__main__":
    unittest.main()
