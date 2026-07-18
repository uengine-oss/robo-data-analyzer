from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step4_linking.link_calls.call_classifier import classify_call_sites


class FunctionMacroCallTests(unittest.TestCase):
    def test_declared_function_macro_is_not_external_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.c"
            path.write_text(
                "#define TRACE(...) log_trace(__VA_ARGS__)\n"
                "void run(void) { TRACE(\"running\"); }\n",
                encoding="utf-8",
            )
            caller = CodeNode(
                id="code:sample.c:run", name="run", owner_id="code:sample.c",
                signature="void run(void)", file_path=str(path), start_line=2,
                end_line=2, label="FUNCTION",
            )
            result = classify_call_sites([{
                "caller_id": caller.id,
                "call_text": "TRACE",
                "file": str(path),
                "line": 2,
            }], [caller])

        self.assertEqual([], result.edges)
        self.assertEqual(1, result.dropped_macro)

    def test_object_macro_and_undeclared_name_remain_external(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.c"
            path.write_text(
                "#define CALLBACK (runtime_callback)\n"
                "void run(void) { CALLBACK(); customer_hook(); }\n",
                encoding="utf-8",
            )
            caller = CodeNode(
                id="code:sample.c:run", name="run", owner_id="code:sample.c",
                signature="void run(void)", file_path=str(path), start_line=2,
                end_line=2, label="FUNCTION",
            )
            sites = [
                {"caller_id": caller.id, "call_text": name, "file": str(path), "line": 2}
                for name in ("CALLBACK", "customer_hook")
            ]
            result = classify_call_sites(sites, [caller])

        self.assertEqual(
            [("CALLBACK", "external"), ("customer_hook", "external")],
            [(edge.callee_id, edge.scope) for edge in result.edges],
        )
        self.assertEqual(0, result.dropped_macro)


if __name__ == "__main__":
    unittest.main()
