from __future__ import annotations

import unittest
from unittest import mock
from types import SimpleNamespace

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import BatchSummaryItem
from pipeline.meaning.batch_analyze import _analyze_batch
from pipeline.meaning.batch_planner import Batch
from shared.concurrency.priority_semaphore import PrioritySemaphore
from tests.pipeline.semantic_frame_fixtures import semantic_frame_for_node


def _node(label: str, line: int, *, expression: str = "", name: str = "") -> CodeNode:
    return CodeNode(
        id=f"node:{label}:{line}", signature=label, file_path="sample.sql",
        start_line=line, end_line=line, label=label, expression=expression, name=name,
    )


class StructuralLeafSummaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_structural_leaf_reaches_model_owned_summary_concern(self) -> None:
        nodes = (
            _node("COMMIT", 7),
            _node("EXIT", 8, expression="C_DATA%NOTFOUND"),
            _node("CALL", 9, name="DBMS_OUTPUT.PUT_LINE"),
        )
        contexts = {
            node.id: AnalysisContext(
                id=node.id, signature=node.signature, file_path=node.file_path,
                start_line=node.start_line, end_line=node.end_line,
            )
            for node in nodes
        }
        seen: list[tuple[str, ...]] = []

        async def _collect(**kwargs):
            seen.append(tuple(kwargs["node_ids"]))
            return {
                node_id: BatchSummaryItem(
                    node_key=f"{contexts[node_id].start_line}-{contexts[node_id].end_line}",
                    summary=(
                        f"{node_id}의 직접 동작을 수행한다. "
                        f"{code_by_id[node_id].expression or code_by_id[node_id].name}"
                    ),
                    stereotype="Helper",
                    evidence_status="sufficient",
                    missing_context=[],
                    semantic_frame=semantic_frame_for_node(
                        code_by_id[node_id], meaning=f"{node_id}의 직접 동작을 수행한다",
                    ),
                )
                for node_id in kwargs["node_ids"]
            }

        strategy = SimpleNamespace(prompts_dir="pipeline/meaning/dbms", name="dbms")
        code_by_id = {node.id: node for node in nodes}
        target = "pipeline.meaning.batch_analyze"
        with mock.patch(f"{target}._collect_keyed_concern", _collect):
            result = await _analyze_batch(
                Batch(parent_id=None, node_ids=tuple(node.id for node in nodes)),
                contexts, code_by_id, strategy,
                None, {}, mock.Mock(), PrioritySemaphore(64),
                set(), set(), mock.Mock(),
            )

        self.assertEqual([tuple(node.id for node in nodes)], seen)
        self.assertTrue(all(node.summary for node in nodes))
        self.assertEqual({node.id for node in nodes}, {node.id for node in result})


if __name__ == "__main__":
    unittest.main()
