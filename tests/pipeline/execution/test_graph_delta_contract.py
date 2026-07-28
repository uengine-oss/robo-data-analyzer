import unittest
from dataclasses import dataclass

from pydantic import ValidationError

from graph.schema.graph_edges import (
    Calls, HasChunk, HasExample, HasRule, ReadsWrites, ReferTo, StatementNext,
)
from graph.schema.graph_nodes import (
    Example, ExternalChunk, ProjectFile, Question, Rule,
)
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
    async def test_external_nodes_have_exact_labels_and_display_properties(self) -> None:
        stream = _CaptureStream()
        nodes = [
            ProjectFile("file:shop/a.dat", "a.dat", "shop", "a.dat", ".dat", "custom", "analyzed"),
            ExternalChunk(
                id="file:shop/a.yaml:chunk:0-23", name="first", keys=("first", "first.enabled"),
                semantic_kind="yaml_entry", text="first:\n  enabled: true\n", file_path="a.yaml",
                char_start=0, char_end=23, start_line=1, end_line=2,
                provenance="source_span", source_hash="b" * 64, content_hash="a" * 64,
            ),
        ]
        await emit_nodes(stream, nodes)
        self.assertEqual([event["label"] for event in stream.events], [
            "PROJECT_FILE", "EXTERNAL_CHUNK",
        ])
        self.assertEqual(stream.events[0]["props"]["status"], "analyzed")
        self.assertEqual(stream.events[1]["props"]["semantic_kind"], "yaml_entry")
        self.assertEqual(stream.events[1]["props"]["provenance"], "source_span")
        self.assertEqual(stream.events[1]["props"]["text"], "first:\n  enabled: true\n")

    async def test_external_edges_have_exact_wire_shapes(self) -> None:
        stream = _CaptureStream()
        edges = [
            ReferTo("code:f", "file:chunk", matched_key="first.enabled",
                    evidence_file="A.java", evidence_line=5, via_symbol="KEY"),
            HasChunk("file:root", "file:chunk"),
        ]
        await emit_edges(stream, edges)
        self.assertEqual([event["rel_type"] for event in stream.events], [
            "REFER_TO", "HAS_CHUNK",
        ])
        self.assertEqual(stream.events[0]["props"], {
            "matched_key": "first.enabled", "evidence_file": "A.java",
            "evidence_line": 5, "via_symbol": "KEY",
        })
        self.assertEqual(stream.events[1]["props"], {})

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
            "evidence_lines": [42],
        })

    async def test_has_rule_occurrence_identity_is_streamed(self) -> None:
        stream = _CaptureStream()
        await emit_edges(
            stream,
            [
                HasRule(
                    "function:1",
                    "rule:1",
                    occurrence_id="function:1::R0002",
                    ordinal=2,
                    anchor_line=19,
                )
            ],
        )
        self.assertEqual(
            stream.events[0]["props"],
            {
                "occurrence_id": "function:1::R0002",
                "ordinal": 2,
                "anchor_line": 19,
            },
        )

    async def test_has_example_occurrence_identity_is_streamed(self) -> None:
        stream = _CaptureStream()
        await emit_edges(
            stream,
            [
                HasExample(
                    source_id="rule:shared",
                    target_id="function:1::R0002::example:1",
                    occurrence_id="function:1::R0002",
                    ordinal=1,
                )
            ],
        )
        self.assertEqual(
            stream.events[-1]["props"],
            {
                "occurrence_id": "function:1::R0002",
                "ordinal": 1,
            },
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
