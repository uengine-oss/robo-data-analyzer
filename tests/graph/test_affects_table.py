"""AFFECTS_TABLE exact endpoint identity contract tests."""

import unittest

from graph.edges.affects_table import _UPSERT_QUERY, upsert_affects_table
from graph.schema.graph_edges import AffectsTable
from pipeline.execution.graph_delta_stream import _edge_wire
from pipeline.steps.step7_analyze_code.example_mapping_builder import (
    ExampleMappingResult,
    _collect_affects_table,
)


class _RecordingAnalysisGraphDatabase:
    def __init__(self) -> None:
        self.query = ""
        self.items: list[dict] = []

    async def run_batch_unwind(self, query: str, items: list[dict]) -> None:
        self.query = query
        self.items = items


class AffectsTableContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_table_id_and_owner_are_preserved(self) -> None:
        graph_database = _RecordingAnalysisGraphDatabase()
        await upsert_affects_table(graph_database, [
            AffectsTable(
                source_id="code:orders::rule::1",
                target_id="db:shop.public.orders",
                access="WRITE",
                op="UPDATE",
                op_source="SCANNER",
            ),
        ])

        self.assertEqual(graph_database.items, [{
            "source_id": "code:orders::rule::1",
            "target_id": "db:shop.public.orders",
            "access": "WRITE",
            "op": "UPDATE",
            "op_source": "SCANNER",
        }])
        self.assertIn("t:TABLE {id: item.target_id", graph_database.query)
        self.assertIn("graph_owner: 'analyzer'", graph_database.query)
        self.assertNotIn("t.name", graph_database.query)
        self.assertNotIn("split(", graph_database.query)
        # spec 116: 귀속 소스는 RULE 노드다 (규칙 단위 결정론 귀속)
        self.assertIn("r:RULE {id: item.source_id", graph_database.query)
        self.assertIn("a.access = item.access", graph_database.query)
        self.assertIn("a.op_source = item.op_source", graph_database.query)

    def test_query_constant_uses_exact_identity(self) -> None:
        self.assertIn("item.target_id", _UPSERT_QUERY)

    def test_live_delta_preserves_effect_contract(self) -> None:
        edge = AffectsTable(
            source_id="example",
            target_id="table",
            access="WRITE",
            op="UNKNOWN",
            op_source="UNRESOLVED",
        )
        self.assertEqual(
            {
                "access": "WRITE",
                "op": "UNKNOWN",
                "op_source": "UNRESOLVED",
            },
            _edge_wire(edge)[3],
        )

    def test_builder_requires_complete_structured_transport(self) -> None:
        result = ExampleMappingResult()
        _collect_affects_table(
            "example",
            [{
                "table": "table",
                "access": "WRITE",
                "op": "INSERT",
                "op_source": "LLM_INFERRED",
            }],
            result,
        )
        self.assertEqual("LLM_INFERRED", result.affects_table[0].op_source)
        with self.assertRaisesRegex(ValueError, "incomplete AFFECTS_TABLE transport"):
            _collect_affects_table(
                "legacy",
                [{"table": "table", "op": "INSERT"}],
                ExampleMappingResult(),
            )


if __name__ == "__main__":
    unittest.main()
