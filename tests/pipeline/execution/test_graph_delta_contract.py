import unittest
from dataclasses import dataclass

from pydantic import ValidationError

from graph.schema.graph_edges import (
    Calls, HasRule, ReadsWrites, StatementNext,
)
from graph.schema.graph_nodes import Rule
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
                "anchor_line": 1,
                "cond": 'F("stock") == 0',
                "then": [],
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
            Rule(
                id="fn:1::R-19", anchor_line=19,
                structural_condition='F("stock") == 0',
                structural_effects=('call("reject", None, [])',),
                condition_description="재고가 없을 때",
                effect_descriptions=("주문을 거절한다",),
            ),
        ])
        await emit_edges(stream, [HasRule("function:1", "fn:1::R-19")])

        self.assertEqual(stream.events[0]["label"], "RULE")
        self.assertNotIn("nl", stream.events[0]["props"])
        self.assertEqual(stream.events[0]["props"]["line"], 19)
        self.assertEqual(
            list(stream.events[0]["props"]["effects"]),
            ['call("reject", None, [])'],
        )
        self.assertEqual(stream.events[1]["props"], {"order": 0})

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

        self.assertEqual(stream.events[0]["props"], {"operations": ["SELECT"]})

    async def test_has_rule_streams_only_independent_ordinal(self) -> None:
        stream = _CaptureStream()
        await emit_edges(
            stream,
            [
                HasRule(
                    "function:1",
                    "rule:1",
                    ordinal=2,
                )
            ],
        )
        self.assertEqual(
            stream.events[0]["props"],
            {"order": 2},
        )

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
