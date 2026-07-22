import asyncio

from graph.schema.graph_nodes import CodeNode, Module
from pipeline.execution.graph_delta_stream import emit_nodes
import unittest


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


def test_code_node_event_keeps_owner_without_internal_analysis_material() -> None:
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
        search_text="internal search material",
        domain_terms={"internal": "domain material"},
        embedding=(0.1, 0.2),
    )]))
    props = stream.events[0]["props"]
    assert props["owner_id"] == "code:mall/shipping.c"
    assert "search_text" not in props
    assert "domain_terms" not in props
    assert "embedding" not in props


if __name__ == "__main__":
    test_file_node_event_keeps_source_identity()
    test_code_node_event_keeps_owner_without_internal_analysis_material()
    print("[OK] graph stream source identity")


# ── 스위트 편입 브리지 (2026-07-22 테스트 정리) ──────────────────────────
# 함수식 케이스는 unittest discover 에 수집되지 않아 스위트에서 실행되지 않는
# 일회성이었다. 아래 브리지가 전 케이스를 스위트 회귀로 편입한다 (재사용 보장).
class ModuleCasesSuiteBridge(unittest.TestCase):
    def test_module_cases(self):
        import asyncio
        import inspect
        cases = [(n, f) for n, f in sorted(globals().items())
                 if n.startswith("test_") and inspect.isfunction(f)]
        self.assertTrue(cases, "함수식 케이스가 없다")
        for name, fn in cases:
            with self.subTest(case=name):
                if inspect.iscoroutinefunction(fn):
                    asyncio.run(fn())
                else:
                    fn()
