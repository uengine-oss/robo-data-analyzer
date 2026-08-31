from __future__ import annotations

import unittest

from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import (
    DataObjectUsageEvidence,
    QualifiedColumnUsage,
)
from pipeline.semantic_tasks.table.usage import project_function_table_usages
from pipeline.models.semantic_frame import SemanticFrame, SemanticSlot
from pipeline.publication.function_meanings import build_function_meanings_write_batch
from pipeline.semantic_tasks.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)


SCOPE = "analysis-scope/v1:" + "a" * 64
GENERATION = "run:20260828"
TABLE = "db:skt.public.ORDERS"


def _function_and_frame() -> tuple[CodeNode, SemanticFrame]:
    function = CodeNode(
        id="code:orders.sync",
        signature="sync(ctx)",
        file_path="orders.c",
        start_line=10,
        end_line=40,
        label="FUNCTION",
        name="sync",
        owner_id="module:orders.c",
        summary="주문 상태를 확인하고 처리 결과를 저장한다.",
        stereotype="Service",
        meaning_status="sufficient",
    )
    context = AnalysisContext(
        id=function.id,
        signature=function.signature,
        file_path=function.file_path,
        start_line=function.start_line,
        end_line=function.end_line,
        direct_reads=(TABLE,),
        direct_writes=(TABLE,),
        reads=(TABLE,),
        writes=(TABLE,),
        data_object_usages=(
            DataObjectUsageEvidence(
                source_id=function.id,
                raw_reference="ORDERS O",
                name="ORDERS",
                access="READ",
                evidence_file=function.file_path,
                evidence_line=17,
                table_id=TABLE,
                resolution="exact",
                qualified_columns=(QualifiedColumnUsage(
                    raw_reference="O.STATUS",
                    qualifier="O",
                    name="STATUS",
                    evidence_line=17,
                ),),
            ),
            DataObjectUsageEvidence(
                source_id=function.id,
                raw_reference="ORDERS O",
                name="ORDERS",
                access="WRITE",
                evidence_file=function.file_path,
                evidence_line=29,
                table_id=TABLE,
                resolution="exact",
                qualified_columns=(QualifiedColumnUsage(
                    raw_reference="O.RESULT_CODE",
                    qualifier="O",
                    name="RESULT_CODE",
                    evidence_line=29,
                ),),
            ),
        ),
    )
    context.extra["ast_statements"] = ()
    context.extra["table_op"] = {TABLE: "UPDATE"}
    profile, all_specs = build_code_semantic_plan(function, context)
    usage_specs = semantic_concern_slots(all_specs)["table_usage_meanings"]
    slots = {}
    for spec in usage_specs:
        fact = next(
            profile.evidence[ref]
            for ref in spec.evidence_refs
            if profile.evidence[ref].kind == "DATA_OBJECT"
        )
        access = str(fact.attributes["access"])
        meaning = (
            "처리 가능 상태인지 주문을 조회한다."
            if access == "READ"
            else "처리 결과 코드를 주문에 저장한다."
        )
        slots[spec.slot_id] = SemanticSlot(
            slot_id=spec.slot_id,
            family=spec.family,
            role=spec.role,
            target_ref=spec.target_ref,
            evidence_refs=spec.evidence_refs,
            meaning=meaning,
            status="sufficient",
        )
    frame = SemanticFrame(
        target=profile.target,
        profile=profile,
        slots=slots,
    )
    return function, frame


class FunctionTableUsageProjectionTest(unittest.TestCase):
    def test_same_table_read_and_write_remain_two_meanings(self) -> None:
        function, frame = _function_and_frame()
        usages = project_function_table_usages(
            frame,
            direct_reads=(TABLE,),
            direct_writes=(TABLE,),
        )

        self.assertEqual(["read", "write"], [item.access_mode for item in usages])
        self.assertEqual(
            "처리 가능 상태인지 주문을 조회한다.",
            usages[0].function_table_usage_summary,
        )
        self.assertEqual(
            "처리 결과 코드를 주문에 저장한다.",
            usages[1].function_table_usage_summary,
        )
        self.assertEqual((f"{TABLE}.STATUS",), usages[0].used_column_ids)
        self.assertEqual((f"{TABLE}.RESULT_CODE",), usages[1].used_column_ids)
        self.assertNotEqual(
            usages[0].data_usage_occurrence_refs,
            usages[1].data_usage_occurrence_refs,
        )
        function.extra["function_table_usages"] = [
            item.model_dump() for item in usages
        ]

        batch = build_function_meanings_write_batch(
            analysis_scope_id=SCOPE,
            generation_id=GENERATION,
            code_nodes=[function],
            reads_writes_edges=[
                ReadsWrites(function.id, TABLE, "READS", "SELECT", "orders.c", 17),
                ReadsWrites(function.id, TABLE, "WRITES", "UPDATE", "orders.c", 29),
            ],
        )
        relation_expectations = [
            item for item in batch.expectations
            if item.identity.canonical_key().startswith("relationship")
        ]
        self.assertEqual(2, len(relation_expectations))
        summaries = {
            item.identity.relationship_type: item.property_map[
                "function_table_usage_summary"
            ]
            for item in relation_expectations
        }
        self.assertEqual(
            {
                "READS": "처리 가능 상태인지 주문을 조회한다.",
                "WRITES": "처리 결과 코드를 주문에 저장한다.",
            },
            summaries,
        )

    def test_missing_access_result_is_not_silently_shared(self) -> None:
        function, frame = _function_and_frame()
        usages = project_function_table_usages(
            frame,
            direct_reads=(TABLE,),
            direct_writes=(TABLE,),
        )
        function.extra["function_table_usages"] = [usages[0].model_dump()]
        with self.assertRaisesRegex(ValueError, "population differs"):
            build_function_meanings_write_batch(
                analysis_scope_id=SCOPE,
                generation_id=GENERATION,
                code_nodes=[function],
                reads_writes_edges=[
                    ReadsWrites(function.id, TABLE, "READS", "SELECT"),
                    ReadsWrites(function.id, TABLE, "WRITES", "UPDATE"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
