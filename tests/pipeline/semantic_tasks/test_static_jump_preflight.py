from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from graph.schema.graph_nodes import CodeNode
from pipeline.execution.analysis_handoff import AnalysisHandoff
from pipeline.models.analysis_context import AnalysisContext, StatementFact
from pipeline.semantic_tasks.step_entry import analyze_code_step
from shared.concurrency.priority_semaphore import PrioritySemaphore


class StaticJumpPreflightTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_goto_ast_fails_before_semantic_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.c"
            source.write_text("goto done;\ndone:\n", encoding="utf-8")
            node = CodeNode(
                id="code:sample.c:f", name="f", signature="f()",
                file_path=str(source), start_line=1, end_line=2,
                label="FUNCTION",
            )
            context = AnalysisContext(
                id=node.id, signature=node.signature, file_path=node.file_path,
                start_line=1, end_line=2,
            )
            context.extra["ast_statements"] = (
                StatementFact("goto", "GOTO", 1, 1, target="done"),
            )

            code_analyzer = AsyncMock()
            with self.assertRaisesRegex(
                ValueError,
                r"static jump target contract failed.*code:sample.c:f.*GOTO@1",
            ):
                await analyze_code_step(
                    {
                        "analysis_contexts": {node.id: context},
                        "code_nodes": [node],
                        "tables": [],
                        "columns": [],
                        "has_column_edges": [],
                        "modules": [],
                    },
                    {"configurable": {
                        "strategy": SimpleNamespace(
                            name="framework", code_analyzer=code_analyzer,
                        ),
                        "analysis_handoff": AnalysisHandoff(PrioritySemaphore(1)),
                    }},
                )

            code_analyzer.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()
