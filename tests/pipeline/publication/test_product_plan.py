from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.publication_schema import PRODUCT_STAGE_NAMES
from graph.schema.graph_nodes import Module
from pipeline.models.external_reference_resolution import (
    ExternalReferenceResolutionLedger,
)
from pipeline.models.human_glossary import HumanGlossary
from pipeline.publication.product_plan import (
    CompleteProductPlanInput,
    build_complete_product_write_plan,
)
from pipeline.steps.step2_load_ast.source_ast_closure import (
    SourceAstArtifact,
    SourceAstClosure,
)


class CompleteProductPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        source_root = root / "source"
        source_path = source_root / "sample.c"
        self.closure = SourceAstClosure(
            source_root=source_root,
            ast_root=root / "analysis",
            artifacts=(SourceAstArtifact(
                source_id="sample.c",
                source_path=source_path,
                ast_path=root / "analysis" / "sample.c.json",
                diagnostic_path=root / "diagnostics" / "sample.c.parse.json",
                source_sha256="a" * 64,
                ast_sha256="b" * 64,
                diagnostic_sha256="c" * 64,
                language="c",
                ast_document={"source": "sample.c", "nodes": []},
            ),),
        )
        self.module = Module(
            id="module:sample.c",
            name="sample.c",
            file_path=str(source_path),
            label="FILE",
            start_line=0,
            end_line=0,
        )

    def _input(self) -> CompleteProductPlanInput:
        return CompleteProductPlanInput(
            analysis_scope_id="scope-1",
            generation_id="generation-1",
            source_ast_closure=self.closure,
            human_glossary=HumanGlossary(
                self.closure.source_root / "analysis" / "glossary.yaml",
                False,
                None,
                None,
                (),
            ),
            packages=(),
            modules=(self.module,),
            code_nodes=(),
            belongs_to_edges=(),
            has_member_edges=(),
            parent_of_edges=(),
            statement_next_edges=(),
            tables=(),
            columns=(),
            has_column_edges=(),
            foreign_key_edges=(),
            external_source_records=(),
            resolved_external_reference_links=(),
            external_reference_ledger=ExternalReferenceResolutionLedger(
                required_external_reference_occurrence_ids=(),
                occurrences=(),
            ),
            calls_edges=(),
            call_resolution_ledger=(),
            reads_writes_edges=(),
            rules=(),
            examples=(),
            has_rule_edges=(),
            has_example_edges=(),
            component_member_ids_by_component_id={},
            search_segments=(),
            prepublish_wall_time_ms=123,
            prepublish_performance_artifact_hash="d" * 64,
        )

    def test_every_closed_product_stage_is_present_exactly_once(self) -> None:
        plan = build_complete_product_write_plan(self._input())

        self.assertEqual(
            PRODUCT_STAGE_NAMES,
            {batch.stage_name for batch in plan.batches},
        )
        self.assertEqual(len(PRODUCT_STAGE_NAMES), len(plan.batches))
        self.assertIn(
            "node\x1fAnalysisHealth\x1fgeneration-1::health",
            dict(plan.required_product_record_hash_by_identity),
        )

    def test_batch_order_preserves_create_before_relationship_and_patch_dependencies(self) -> None:
        plan = build_complete_product_write_plan(self._input())

        self.assertEqual(
            [
                "code_structure",
                "data_schema_structure",
                "external_chunks",
                "call_links",
                "data_access_links",
                "function_meanings",
                "table_meanings",
                "rule_meanings",
                "examples",
                "module_summary_rollups",
                "components",
                "glossary",
                "search_segments",
                "analysis_health",
            ],
            [batch.stage_name for batch in plan.batches],
        )

    def test_same_complete_input_has_the_same_exact_plan(self) -> None:
        first = build_complete_product_write_plan(self._input())
        second = build_complete_product_write_plan(self._input())

        self.assertEqual(
            first.required_operation_key_set,
            second.required_operation_key_set,
        )
        self.assertEqual(
            first.required_product_record_hash_by_identity,
            second.required_product_record_hash_by_identity,
        )


if __name__ == "__main__":
    unittest.main()
