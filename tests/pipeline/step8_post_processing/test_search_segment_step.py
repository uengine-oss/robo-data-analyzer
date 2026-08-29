from __future__ import annotations

import unittest
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step8_post_processing.build_search_segments_step import (
    _build_search_segments,
)


def _node(
    stable_id: str,
    *,
    label: str = "FUNCTION",
    summary: str | None = "주문을 검증한다.",
) -> CodeNode:
    return CodeNode(
        id=stable_id,
        signature="validate(void)",
        file_path="orders.c",
        start_line=3,
        end_line=9,
        label=label,
        name="validate",
        owner_id="module:orders.c",
        summary=summary,
    )


class SearchSegmentStepTest(unittest.TestCase):
    def test_routine_summary_is_embedded_once_without_graph_input(self) -> None:
        with patch(
            "pipeline.steps.step8_post_processing.build_search_segments_step.embedder.encode_texts",
            return_value=[[0.25, -0.5]],
        ) as encode:
            segments = _build_search_segments(
                [_node("code:a"), _node("code:a:if", label="IF")],
                "embed-v1",
            )

        encode.assert_called_once_with(["주문을 검증한다."])
        self.assertEqual(1, len(segments))
        self.assertEqual("code:a", segments[0]["parent_stable_id"])
        self.assertEqual([0.25, -0.5], segments[0]["embedding"])
        self.assertEqual(3, segments[0]["source_start_line"])

    def test_missing_summary_is_a_legitimate_empty_search_population(self) -> None:
        with patch(
            "pipeline.steps.step8_post_processing.build_search_segments_step.embedder.encode_texts",
        ) as encode:
            segments = _build_search_segments(
                [_node("code:a", summary=None)],
                "embed-v1",
            )
        self.assertEqual([], segments)
        encode.assert_not_called()

    def test_embedding_population_mismatch_fails_closed(self) -> None:
        with patch(
            "pipeline.steps.step8_post_processing.build_search_segments_step.embedder.encode_texts",
            return_value=[],
        ), self.assertRaisesRegex(ValueError, "different population"):
            _build_search_segments([_node("code:a")], "embed-v1")


if __name__ == "__main__":
    unittest.main()
