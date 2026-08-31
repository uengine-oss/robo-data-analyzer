from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from llm.usage.cost_budget import CostBudget
from pipeline.execution.step_config import StepConfigurationError
from pipeline.steps.step8_post_processing.build_search_segments_step import (
    _build_search_segments,
    build_search_segments_step,
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

    def test_paid_embedding_receives_run_usage_and_budget_context(self) -> None:
        usage_path = Path("run") / "usage.jsonl"
        budget = CostBudget(limit_usd=1.0)
        with patch(
            "pipeline.steps.step8_post_processing.build_search_segments_step.embedder.encode_texts",
            return_value=[[0.25, -0.5]],
        ) as encode:
            _build_search_segments(
                [_node("code:a")],
                "openai:text-embedding-3-small",
                usage_log_path=usage_path,
                cost_budget=budget,
            )

        encode.assert_called_once_with(
            ["주문을 검증한다."],
            usage_log_path=usage_path,
            cost_budget=budget,
            agent_name="embedding:search_segments",
        )

    def test_openai_step_passes_run_usage_path_and_shared_budget(self) -> None:
        budget = CostBudget(limit_usd=1.0)
        run = SimpleNamespace(run_dir=Path("sealed-run"))
        settings = SimpleNamespace(
            analysis_pipeline=SimpleNamespace(embed_enabled=True),
            embedding=SimpleNamespace(provider="openai"),
        )
        with (
            patch(
                "pipeline.steps.step8_post_processing.build_search_segments_step.SETTINGS",
                settings,
            ),
            patch(
                "pipeline.steps.step8_post_processing.build_search_segments_step.embedder.fingerprint",
                return_value="openai:text-embedding-3-small",
            ),
            patch(
                "pipeline.steps.step8_post_processing.build_search_segments_step._build_search_segments",
                return_value=[],
            ) as build,
        ):
            result = asyncio.run(build_search_segments_step(
                {"code_nodes": [_node("code:a")]},
                {"configurable": {"run": run, "cost_budget": budget}},
            ))

        self.assertEqual({"search_segments": []}, result)
        build.assert_called_once_with(
            [_node("code:a")],
            "openai:text-embedding-3-small",
            usage_log_path=Path("sealed-run") / "usage.jsonl",
            cost_budget=budget,
        )

    def test_openai_step_rejects_missing_run_accounting_context(self) -> None:
        settings = SimpleNamespace(
            analysis_pipeline=SimpleNamespace(embed_enabled=True),
            embedding=SimpleNamespace(provider="openai"),
        )
        with (
            patch(
                "pipeline.steps.step8_post_processing.build_search_segments_step.SETTINGS",
                settings,
            ),
            self.assertRaisesRegex(StepConfigurationError, "requires.*'run'"),
        ):
            asyncio.run(build_search_segments_step({"code_nodes": []}, {}))


if __name__ == "__main__":
    unittest.main()
