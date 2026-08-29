from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.publication.components import (
    ComponentsProjectionError,
    build_components_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


def _routine(stable_id: str) -> CodeNode:
    return CodeNode(
        id=stable_id,
        signature=f"{stable_id}(void)",
        file_path="orders.c",
        start_line=1,
        end_line=10,
        label="FUNCTION",
        name=stable_id,
        owner_id="module:orders.c",
    )


class ComponentsPublicationTest(unittest.TestCase):
    def test_component_publishes_only_identity_and_membership(self) -> None:
        routines = [_routine("code:a"), _routine("code:b")]
        batch = build_components_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=routines,
            member_ids_by_component_id={
                "COMPONENT:code:a": ["code:a", "code:b"],
            },
        )

        self.assertEqual(1, len(batch.node_create_operations))
        self.assertEqual(2, len(batch.relationship_create_operations))
        properties = dict(batch.node_create_operations[0].present_properties)
        self.assertEqual(
            {"stable_id", "analysis_scope_id", "generation_id"},
            set(properties),
        )
        self.assertEqual(
            {"MEMBER_OF_COMPONENT"},
            {
                operation.identity.relationship_type
                for operation in batch.relationship_create_operations
            },
        )

    def test_member_must_be_sorted_unique_routine_with_one_owner(self) -> None:
        routines = [_routine("code:a"), _routine("code:b"), _routine("code:c")]
        cases = (
            ({"COMPONENT:code:a": ["code:b", "code:a"]}, "unique and sorted"),
            ({"COMPONENT:code:a": ["code:a", "missing"]}, "verified routine"),
            ({
                "COMPONENT:code:a": ["code:a", "code:b"],
                "COMPONENT:code:b": ["code:b", "code:c"],
            }, "multiple Components"),
            ({"wrong": ["code:a", "code:b"]}, "deterministic member identity"),
        )
        for memberships, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ComponentsProjectionError, message,
            ):
                build_components_write_batch(
                    analysis_scope_id=SCOPE,
                    generation_id=GENERATION,
                    code_nodes=routines,
                    member_ids_by_component_id=memberships,
                )

    def test_no_clusters_is_an_explicit_empty_stage(self) -> None:
        batch = build_components_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[_routine("code:a")],
            member_ids_by_component_id={},
        )
        self.assertEqual((), batch.expectations)
        self.assertEqual("components", batch.stage_name)


if __name__ == "__main__":
    unittest.main()
