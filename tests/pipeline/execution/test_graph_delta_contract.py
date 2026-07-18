import unittest
from dataclasses import dataclass

from pydantic import ValidationError

from graph.schema.graph_edges import Calls, HasRule, ReadsWrites, StatementNext
from graph.schema.graph_nodes import Example, Question, Rule
from pipeline.models.code_analysis_output import RuleItem
from pipeline.execution.graph_delta_stream import emit_edges, emit_nodes


class _CaptureStream:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)

    async def emit_async(self, event: dict) -> None:
        self.events.append(event)


@dataclass(frozen=True)
class _UnsupportedEdge:
    source_id: str
    target_id: str


class GraphDeltaContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_removed_coupled_domains_llm_field_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RuleItem.model_validate({
                "statement": "재고가 없으면 주문을 거절한다",
                "coupled_domains": ["재고"],
                "examples": [],
            })

    async def test_persisted_next_edge_is_emitted_with_exact_endpoints(self) -> None:
        stream = _CaptureStream()

        emitted = await emit_edges(stream, [StatementNext("stmt:1", "stmt:2")])

        self.assertEqual(emitted, 1)
        self.assertEqual(stream.events, [{
            "type": "relationship_event",
            "op": "merge",
            "rel_type": "NEXT",
            "from": {"id": "stmt:1"},
            "to": {"id": "stmt:2"},
            "props": {},
        }])

    async def test_semantic_nodes_and_has_rule_properties_match_persisted_graph(self) -> None:
        stream = _CaptureStream()

        await emit_nodes(stream, [
            Rule(id="rule:1", statement="재고가 부족하면 주문을 거절한다"),
            Example(id="example:1", given="재고 0", when="주문", then="거절"),
            Question(id="question:1", text="재입고 예정인가?", reason="판단 근거"),
        ])
        await emit_edges(stream, [HasRule("function:1", "rule:1")])

        self.assertEqual([event["label"] for event in stream.events[:3]], [
            "RULE", "EXAMPLE", "QUESTION",
        ])
        self.assertEqual(stream.events[0]["props"]["statement"], "재고가 부족하면 주문을 거절한다")
        self.assertEqual(stream.events[1]["props"]["then"], "거절")
        self.assertEqual(stream.events[2]["props"]["reason"], "판단 근거")
        self.assertEqual(stream.events[3]["props"], {})

    async def test_reads_properties_match_persisted_graph(self) -> None:
        stream = _CaptureStream()
        edge = ReadsWrites(
            source_id="select:1",
            target_id="db.public.orders",
            relation="READS",
            dml_type="SELECT",
            evidence_file="orders.sql",
            evidence_line=42,
        )

        await emit_edges(stream, [edge])

        self.assertEqual(stream.events[0]["props"], {
            "dml_type": "SELECT",
            "evidence_file": "orders.sql",
            "evidence_line": 42,
        })

    async def test_unsupported_and_blank_endpoint_edges_fail_closed(self) -> None:
        stream = _CaptureStream()
        with self.assertRaises(ValueError):
            await emit_edges(stream, [_UnsupportedEdge("a", "b")])
        with self.assertRaises(ValueError):
            await emit_edges(stream, [StatementNext("a", "")])

    async def test_calls_cannot_bypass_dedicated_recursion_and_external_mapping(self) -> None:
        stream = _CaptureStream()

        with self.assertRaises(ValueError):
            await emit_edges(stream, [Calls("fn:a", "fn:b", scope="internal")])


if __name__ == "__main__":
    unittest.main()
