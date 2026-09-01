from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from graph.schema.graph_nodes import CodeNode
from llm.usage.cost_budget import CostBudget
from pipeline.execution.step_config import StepConfigurationError
from pipeline.stages.output.build_search_embeddings_step import (
    _build_search_embeddings,
    build_search_embeddings_step,
)


def _node(node_id: str, *, label: str = "FUNCTION", summary="주문을 검증한다."):
    return CodeNode(
        id=node_id,
        signature="validate(void)",
        file_path="orders.c",
        start_line=3,
        end_line=9,
        label=label,
        name="validate",
        owner_id="module:orders.c",
        summary=summary,
    )


class SearchEmbeddingsStepTest(unittest.TestCase):
    def test_only_routines_receive_direct_vectors(self) -> None:
        with patch(
            "pipeline.stages.output.build_search_embeddings_step.text_embedder.encode_texts",
            return_value=[[0.25, -0.5]],
        ) as encode:
            result = _build_search_embeddings([
                _node("code:a"), _node("code:a:if", label="IF"),
            ])
        encode.assert_called_once_with(["주문을 검증한다."])
        self.assertEqual([
            {"routine_id": "code:a", "embedding": [0.25, -0.5]},
        ], result)

    def test_missing_summary_produces_no_vector(self) -> None:
        with patch(
            "pipeline.stages.output.build_search_embeddings_step.text_embedder.encode_texts",
        ) as encode:
            self.assertEqual([], _build_search_embeddings([
                _node("code:a", summary=None),
            ]))
        encode.assert_not_called()

    def test_paid_embedding_receives_run_accounting(self) -> None:
        usage_path = Path("run") / "usage.jsonl"
        budget = CostBudget(limit_usd=1.0)
        with patch(
            "pipeline.stages.output.build_search_embeddings_step.text_embedder.encode_texts",
            return_value=[[0.25, -0.5]],
        ) as encode:
            _build_search_embeddings(
                [_node("code:a")],
                usage_log_path=usage_path,
                cost_budget=budget,
            )
        encode.assert_called_once_with(
            ["주문을 검증한다."],
            usage_log_path=usage_path,
            cost_budget=budget,
            agent_name="embedding:routines",
        )

    def test_step_requires_openai_accounting_context(self) -> None:
        settings = SimpleNamespace(
            analysis_pipeline=SimpleNamespace(embed_enabled=True),
            embedding=SimpleNamespace(provider="openai"),
        )
        with (
            patch(
                "pipeline.stages.output.build_search_embeddings_step.SETTINGS",
                settings,
            ),
            self.assertRaisesRegex(StepConfigurationError, "requires.*'run'"),
        ):
            asyncio.run(build_search_embeddings_step({"code_nodes": []}, {}))


if __name__ == "__main__":
    unittest.main()
