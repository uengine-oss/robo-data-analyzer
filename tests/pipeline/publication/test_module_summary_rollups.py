from __future__ import annotations

import unittest

from graph.schema.graph_nodes import CodeNode, Module
from pipeline.publication.module_summary_rollups import (
    ModuleSummaryRollupsProjectionError,
    build_module_summary_rollups_write_batch,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"


def _module(stable_id: str = "module:orders.c") -> Module:
    return Module(
        id=stable_id,
        name="orders.c",
        file_path="orders.c",
        label="FILE",
    )


def _function(stable_id: str, owner_id: str, summary: str | None) -> CodeNode:
    return CodeNode(
        id=stable_id,
        signature="run(void)",
        file_path="orders.c",
        start_line=1,
        end_line=10,
        label="FUNCTION",
        name="run",
        owner_id=owner_id,
        summary=summary,
    )


class ModuleSummaryRollupsProjectionTest(unittest.TestCase):
    def test_complete_rollup_preserves_sorted_ids_and_summary_bytes(self) -> None:
        module = _module()
        functions = [
            _function("code:z", module.id, "둘째 요약\n원문"),
            _function("code:a", module.id, "첫 요약"),
        ]
        batch = build_module_summary_rollups_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            modules=[module],
            code_nodes=functions,
        )

        properties = batch.expectations[0].property_map
        self.assertEqual(
            ("code:a", "code:z"),
            properties["summary_rollup_function_ids"],
        )
        self.assertEqual(
            ("첫 요약", "둘째 요약\n원문"),
            properties["summary_rollup_summaries"],
        )

    def test_one_missing_summary_publishes_no_partial_rollup(self) -> None:
        module = _module()
        batch = build_module_summary_rollups_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            modules=[module],
            code_nodes=[
                _function("code:a", module.id, "정상 요약"),
                _function("code:b", module.id, None),
            ],
        )
        self.assertEqual((), batch.node_property_set_operations)
        self.assertEqual((), batch.expectations)

    def test_module_without_functions_has_no_rollup_properties(self) -> None:
        batch = build_module_summary_rollups_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            modules=[_module()],
            code_nodes=[],
        )
        self.assertEqual((), batch.node_property_set_operations)

    def test_unknown_owner_and_duplicate_routine_fail_closed(self) -> None:
        module = _module()
        function = _function("code:a", "module:missing", "요약")
        with self.assertRaisesRegex(
            ModuleSummaryRollupsProjectionError, "loaded Module",
        ):
            build_module_summary_rollups_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                modules=[module],
                code_nodes=[function],
            )

        function.owner_id = module.id
        with self.assertRaisesRegex(
            ModuleSummaryRollupsProjectionError, "unique",
        ):
            build_module_summary_rollups_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                modules=[module],
                code_nodes=[function, function],
            )


if __name__ == "__main__":
    unittest.main()
