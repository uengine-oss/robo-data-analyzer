from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.meaning.meaning_fanout import _prepare_analysis_inputs
from pipeline.meaning.meaning_requests import summary_prompt_values
from shared.files.source_cache import SourceFileCache


ROOT = Path(__file__).resolve().parents[3]


class ModelSourceFidelityContractTest(unittest.TestCase):
    def _target(self, directory: str) -> tuple[str, CodeNode, AnalysisContext]:
        source_text = (
            'if (settle_dt == "00000000" && tab == \'\\t\') return 1;\n'
            "if (name == 'O''Brien') return 2;"
        )
        source = Path(directory) / "source.c"
        source.write_text(source_text, encoding="utf-8", newline="")
        node = CodeNode(
            id="code:source.c:check", name="check", signature="check()",
            file_path=str(source), start_line=1, end_line=2, label="FUNCTION",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=node.file_path,
            start_line=1, end_line=2,
        )
        context.extra["ast_statements"] = ()
        return source_text, node, context

    def test_framework_prompt_values_keep_source_literals_byte_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_text, node, context = self._target(directory)
            values, _inventory = _prepare_analysis_inputs(
                context, node, {node.id: context}, {node.id: node},
                SourceFileCache(), {},
            )
            profile, _specs, concerns = context.extra["_semantic_frame_plan"]
            summary_values = summary_prompt_values(
                profile, concerns["summary"], values,
            )

        for line in source_text.splitlines():
            self.assertIn(line, summary_values["code_body"])
        self.assertNotIn("[QUOTED_LITERAL", summary_values["code_body"])

    def test_no_lossy_source_projection_producer_or_replay_switch_exists(self) -> None:
        self.assertFalse(
            (ROOT / "pipeline" / "meaning"
             / "model_source_projection.py").exists()
        )
        self.assertFalse((ROOT / "scripts" / "send_real_prompt_to_gpu.py").exists())


if __name__ == "__main__":
    unittest.main()
