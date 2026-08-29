from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from llm.usage.token_usage import TokenUsageRecord
from search.query import query_llm
from search.query.rewrite import (
    GlossaryRewriteResponse,
    GlossarySubstitution,
    _ccache,
    rewrite_for_cluster,
)


def _glossary() -> dict:
    return {
        "terms": [
            {
                "canonical": "CJ-EXP",
                "colloquial": ["배송망"],
                "meaning": "CJ 배송 시스템",
            },
            {
                "canonical": "HJ-LOG",
                "colloquial": ["배송망"],
                "meaning": "한진 배송 시스템",
            },
        ],
        "actions": [],
    }


class QueryRewriteContractTest(unittest.TestCase):
    def setUp(self) -> None:
        _ccache.clear()

    def tearDown(self) -> None:
        _ccache.clear()

    def test_model_output_contains_only_grounded_substitutions(self) -> None:
        with self.assertRaises(ValidationError):
            GlossaryRewriteResponse.model_validate({
                "intent": "배송 조회",
                "rewritten": "배송 시스템 조회",
                "substitutions": [],
            })

    def test_same_query_is_called_once_and_code_builds_stable_rewrite(self) -> None:
        response = GlossaryRewriteResponse(substitutions=[
            GlossarySubstitution(
                source_text="배송망",
                canonical_term="HJ-LOG",
            ),
            GlossarySubstitution(
                source_text="배송망",
                canonical_term="CJ-EXP",
            ),
        ])
        with (
            patch("search.query.rewrite.build_glossary", return_value=_glossary()),
            patch(
                "search.query.rewrite.query_llm.invoke_structured",
                return_value=response,
            ) as invoke,
        ):
            first = rewrite_for_cluster("배송망 조회")
            second = rewrite_for_cluster("배송망 조회")

        self.assertIs(first, second)
        self.assertEqual("배송망(CJ-EXP·HJ-LOG) 조회", first.rewritten_query)
        self.assertEqual(
            ["CJ-EXP", "HJ-LOG"],
            [item.canonical_term for item in first.glossary_substitutions],
        )
        invoke.assert_called_once()
        self.assertIs(
            GlossaryRewriteResponse,
            invoke.call_args.kwargs["response_model"],
        )

    def test_unconfirmed_model_term_is_rejected_instead_of_hidden(self) -> None:
        response = GlossaryRewriteResponse(substitutions=[
            GlossarySubstitution(
                source_text="배송망",
                canonical_term="INVENTED-NETWORK",
            ),
        ])
        with (
            patch("search.query.rewrite.build_glossary", return_value=_glossary()),
            patch(
                "search.query.rewrite.query_llm.invoke_structured",
                return_value=response,
            ),
            self.assertRaisesRegex(ValueError, "absent from the glossary"),
        ):
            rewrite_for_cluster("배송망 조회")

    def test_structured_boundary_invokes_model_once_and_requires_usage(self) -> None:
        cfg = SimpleNamespace(
            model="search-model",
            provider="openai",
            structured_output_method="json_schema",
        )
        raw_response = object()
        runnable = Mock()
        runnable.invoke.return_value = {
            "raw": raw_response,
            "parsed": {
                "substitutions": [
                    {"source_text": "배송망", "canonical_term": "CJ-EXP"}
                ]
            },
            "parsing_error": None,
        }
        usage = TokenUsageRecord(
            model="search-model",
            input_tokens=12,
            output_tokens=4,
            latency_ms=3,
        )
        with (
            patch.object(query_llm, "_config", return_value=(cfg, "key")),
            patch.object(query_llm, "build_llm_client_from_config") as build,
            patch.object(query_llm, "bind_structured_output", return_value=runnable) as bind,
            patch.object(query_llm, "_record_usage", return_value=usage) as record,
        ):
            response = query_llm.invoke_structured(
                system_prompt="stable instructions",
                user_prompt="배송망 조회",
                response_model=GlossaryRewriteResponse,
            )

        build.assert_called_once_with(cfg, api_key="key")
        bind.assert_called_once()
        runnable.invoke.assert_called_once()
        record.assert_called_once_with(raw_response, "search-model", unittest.mock.ANY)
        self.assertEqual("CJ-EXP", response.substitutions[0].canonical_term)

    def test_structured_call_failure_is_not_replaced_with_original_query(self) -> None:
        cfg = SimpleNamespace(
            model="search-model",
            provider="openai",
            structured_output_method="json_schema",
        )
        runnable = Mock()
        runnable.invoke.side_effect = RuntimeError("provider unavailable")
        with (
            patch.object(query_llm, "_config", return_value=(cfg, "key")),
            patch.object(query_llm, "build_llm_client_from_config"),
            patch.object(query_llm, "bind_structured_output", return_value=runnable),
            self.assertRaisesRegex(RuntimeError, "provider unavailable"),
        ):
            query_llm.invoke_structured(
                system_prompt="stable instructions",
                user_prompt="배송망 조회",
                response_model=GlossaryRewriteResponse,
            )

        runnable.invoke.assert_called_once()


if __name__ == "__main__":
    unittest.main()
