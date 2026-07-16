from __future__ import annotations

import unittest

from graph.schema.graph_nodes import Module, Package
from pipeline.steps.step2_load_ast.node_identity_registry import NodeIdentityCollision, canonicalize_node_lists


class NodeRegistryTest(unittest.TestCase):
    def test_identical_observations_collapse_once(self) -> None:
        package = Package(id="code:mall", name="mall")

        (packages,), collapsed = canonicalize_node_lists(
            [package, package, package]
        )

        self.assertEqual([package], packages)
        self.assertEqual(2, collapsed)

    def test_same_id_with_different_type_is_collision(self) -> None:
        package = Package(id="code:mall", name="mall")
        module = Module(
            id="code:mall",
            name="mall",
            file_path="mall.c",
            label="FILE",
        )

        with self.assertRaises(NodeIdentityCollision):
            canonicalize_node_lists([package], [module])

    def test_same_id_with_different_value_is_collision(self) -> None:
        with self.assertRaises(NodeIdentityCollision):
            canonicalize_node_lists([
                Package(id="code:mall", name="mall"),
                Package(id="code:mall", name="other"),
            ])

    def test_order_and_list_boundaries_are_preserved(self) -> None:
        first = Package(id="code:first", name="first")
        second = Package(id="code:second", name="second")

        (packages, empty), collapsed = canonicalize_node_lists(
            [first, second], []
        )

        self.assertEqual([first, second], packages)
        self.assertEqual([], empty)
        self.assertEqual(0, collapsed)


if __name__ == "__main__":
    unittest.main()
