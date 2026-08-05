import asyncio

from graph.schema.graph_nodes import CodeNode, Module
from pipeline.execution.graph_delta_stream import emit_nodes
import unittest
from tests.unittest_function_cases import load_function_cases


class _Stream:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)

    async def emit_async(self, event: dict) -> None:
        self.events.append(event)


def test_file_node_event_keeps_source_identity() -> None:
    stream = _Stream()
    asyncio.run(emit_nodes(stream, [Module(
        id="code:mall/shopmall.h",
        name="shopmall",
        file_path=r"D:\다운로드\shopmall\mall\shopmall.h",
        label="FILE",
        start_line=0,
        end_line=216,
    )]))
    props = stream.events[0]["props"]
    assert props["file_path"].endswith(r"mall\shopmall.h")
    assert props["end_line"] == 216


def test_code_node_event_keeps_owner_and_public_summary_without_internal_material() -> None:
    stream = _Stream()
    asyncio.run(emit_nodes(stream, [CodeNode(
        id="code:mall/shipping.c:shipping_request",
        name="shipping_request",
        owner_id="code:mall/shipping.c",
        signature="shipping_request()",
        file_path=r"D:\다운로드\shopmall\mall\shipping.c",
        start_line=10,
        end_line=40,
        label="FUNCTION",
        summary="internal semantic material",
        embedding=(0.1, 0.2),
    )]))
    props = stream.events[0]["props"]
    assert props["owner_id"] == "code:mall/shipping.c"
    assert props["summary"] == "internal semantic material"
    assert "domain_terms" not in props
    assert "embedding" not in props


if __name__ == "__main__":
    test_file_node_event_keeps_source_identity()
    test_code_node_event_keeps_owner_and_public_summary_without_internal_material()
    print("[OK] graph stream source identity")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
