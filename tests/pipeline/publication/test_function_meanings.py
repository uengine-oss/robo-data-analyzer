from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.publication.function_meanings import (
    FunctionMeaningsProjectionError,
    build_function_meanings_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


def _function(stable_id: str, **meaning) -> CodeNode:
    node = CodeNode(
        id=stable_id,
        signature="run(void)",
        file_path="run.c",
        start_line=1,
        end_line=20,
        label="FUNCTION",
        name="run",
        owner_id="module:run.c",
    )
    for key, value in meaning.items():
        setattr(node, key, value)
    return node


class FunctionMeaningsProjectionTest(unittest.TestCase):
    def test_sufficient_partial_and_insufficient_presence_is_exact(self) -> None:
        sufficient = _function(
            "code:sufficient",
            summary="주문을 조회한다.",
            stereotype="Query",
            meaning_status="sufficient",
            missing_context_count=0,
        )
        partial = _function(
            "code:partial",
            summary="외부 설정에 따라 주문을 처리한다.",
            stereotype="Service",
            meaning_status="partial",
            missing_context_count=1,
            missing_context_artifact_hash="b" * 64,
        )
        insufficient = _function(
            "code:insufficient",
            meaning_status="insufficient",
            missing_context_count=2,
            missing_context_artifact_hash="c" * 64,
        )

        batch = build_function_meanings_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[partial, insufficient, sufficient],
        )

        properties_by_id = {
            expectation.identity.stable_id: expectation.property_map
            for expectation in batch.expectations
        }
        self.assertEqual(
            {
                "summary",
                "stereotype",
                "meaning_status",
                "missing_context_count",
            },
            set(properties_by_id[sufficient.id]),
        )
        self.assertEqual("partial", properties_by_id[partial.id]["meaning_status"])
        self.assertNotIn("summary", properties_by_id[insufficient.id])
        self.assertNotIn("stereotype", properties_by_id[insufficient.id])
        self.assertEqual(
            "c" * 64,
            properties_by_id[insufficient.id]["missing_context_artifact_hash"],
        )

    def test_non_routine_is_not_given_function_meaning_properties(self) -> None:
        statement = CodeNode(
            id="code:select",
            signature="",
            file_path="query.sql",
            start_line=1,
            end_line=3,
            label="SELECT",
            name="SELECT",
            owner_id="module:query.sql",
        )
        batch = build_function_meanings_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[statement],
        )
        self.assertEqual((), batch.node_property_set_operations)
        self.assertEqual((), batch.expectations)

    def test_invalid_coupled_presence_fails_before_write(self) -> None:
        invalid = _function(
            "code:invalid",
            summary="문장은 있지만 역할이 없다.",
            meaning_status="sufficient",
        )
        with self.assertRaisesRegex(
            FunctionMeaningsProjectionError, "summary/stereotype",
        ):
            build_function_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[invalid],
            )

    def test_duplicate_node_identity_fails_closed(self) -> None:
        function = _function(
            "code:duplicate",
            summary="처리한다.",
            stereotype="Service",
            meaning_status="sufficient",
        )
        with self.assertRaisesRegex(FunctionMeaningsProjectionError, "unique"):
            build_function_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[function, function],
            )


if __name__ == "__main__":
    unittest.main()
