"""Only structurally proved literal constants may feed external links."""
from __future__ import annotations

import unittest

from graph.schema.graph_edges import References
from graph.schema.graph_nodes import Constant
from pipeline.stages.links.external.literal_constants import LiteralConstantIndex


OWNER = "code:pkg:Constants"
ROUTINE = "code:pkg:Service:run"


def _constant(name: str, value: str, *, node_id: str | None = None) -> Constant:
    return Constant(
        id=node_id or f"{OWNER}:{name}", owner_id=OWNER, name=name,
        file_path="Constants.any", start_line=1, end_line=1,
        label="CONSTANT_FIELD", value=value,
    )


class LiteralConstantIndexTest(unittest.TestCase):
    def test_complete_literal_constant_is_available(self) -> None:
        constant = _constant("KEY", '"shop.Order.find"')
        index = LiteralConstantIndex(
            [constant], [References(ROUTINE, constant.id, 10)],
        )
        self.assertEqual("shop.Order.find", index.value_for(ROUTINE, "KEY"))

    def test_concatenation_is_not_value_tracked(self) -> None:
        constant = _constant("KEY", 'PREFIX + "Order.find"')
        index = LiteralConstantIndex(
            [constant], [References(ROUTINE, constant.id, 10)],
        )
        self.assertIsNone(index.value_for(ROUTINE, "KEY"))

    def test_runtime_expression_is_not_value_tracked(self) -> None:
        constant = _constant("KEY", 'getenv("KEY")')
        index = LiteralConstantIndex(
            [constant], [References(ROUTINE, constant.id, 10)],
        )
        self.assertIsNone(index.value_for(ROUTINE, "KEY"))

    def test_unreferenced_literal_is_not_available(self) -> None:
        constant = _constant("KEY", '"shop.Order.find"')
        self.assertIsNone(LiteralConstantIndex([constant]).value_for(ROUTINE, "KEY"))

    def test_same_name_with_two_referenced_declarations_is_ambiguous(self) -> None:
        first = _constant("KEY", '"one"', node_id="constant:one")
        second = _constant("KEY", '"two"', node_id="constant:two")
        index = LiteralConstantIndex(
            [first, second],
            [References(ROUTINE, first.id, 1), References(ROUTINE, second.id, 2)],
        )
        self.assertIsNone(index.value_for(ROUTINE, "KEY"))


if __name__ == "__main__":
    unittest.main()
