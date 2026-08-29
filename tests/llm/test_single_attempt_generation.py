from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel

from llm.runners.model_output_error import ModelOutputError
from llm.client import _build_anthropic_client, _build_openai_compatible_client
from llm.runners.yaml_llm_runner import run_yaml_rule
from pipeline.execution.run_output_context import (
    reset_response_locale,
    set_response_locale,
)


class RemoteProtocolError(Exception):
    pass


class StreamChunkTimeoutError(Exception):
    pass


class _RetryResponse(BaseModel):
    value: str


class ProviderRetryBoundaryTest(unittest.TestCase):
    def _config(self, provider: str):
        return SimpleNamespace(
            provider=provider,
            sampling=SimpleNamespace(top_p=1.0),
            timeout_seconds=30,
            reasoning_effort="none",
            extra_body={},
            verify_ssl=True,
            api_base=None,
        )

    def test_openai_compatible_client_disables_sdk_retries(self) -> None:
        with patch("llm.client.ChatOpenAI") as client:
            _build_openai_compatible_client(
                self._config("openai"), "test-key", "model", 0.0, 100, False,
            )
        self.assertEqual(0, client.call_args.kwargs["max_retries"])

    def test_anthropic_client_disables_sdk_retries(self) -> None:
        with patch("llm.client.ChatAnthropic") as client:
            _build_anthropic_client(
                self._config("anthropic"), "test-key", "model", 0.0, 100, False,
            )
        self.assertEqual(0, client.call_args.kwargs["max_retries"])


class TransportRetryRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._locale_token = set_response_locale("ko")

    async def asyncTearDown(self) -> None:
        reset_response_locale(self._locale_token)

    def _prompts(self, temporary_directory: str) -> Path:
        prompts = Path(temporary_directory)
        (prompts / "boundary.yaml").write_text(
            "system: test\nuser_template: '{value}'\nmodel: test-model\n",
            encoding="utf-8",
        )
        return prompts

    async def test_partial_stream_idle_is_model_output_stall_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts = self._prompts(temporary_directory)

            async def timeout(_messages, config=None):
                config["callbacks"][0].detector.feed('{"value":"partial')
                raise StreamChunkTimeoutError("stream idle")

            runnable = SimpleNamespace(ainvoke=AsyncMock(side_effect=timeout))
            llm = SimpleNamespace(with_structured_output=MagicMock(return_value=runnable))
            with (
                patch(
                    "llm.runners.yaml_llm_runner.load_llm_config",
                    return_value=SimpleNamespace(
                        provider="openai", model="test-model",
                        structured_output_method="function_calling",
                    ),
                ),
                patch("llm.runners.yaml_llm_runner.build_llm_client", return_value=llm),
                patch(
                    "llm.runners.yaml_llm_runner.apply_cache_friendly_filters",
                    side_effect=lambda messages, provider: messages,
                ),
                patch("llm.runners.yaml_llm_runner.response_cache.load", return_value=None),
                patch("llm.runners.yaml_llm_runner._write_error_record") as evidence,
            ):
                with self.assertRaises(ModelOutputError) as caught:
                    await run_yaml_rule(
                        prompts_dir=prompts,
                        rule_name="boundary",
                        user_values={"value": "input"},
                        response_model=_RetryResponse,
                    )

        self.assertEqual("stalled", caught.exception.kind)
        self.assertEqual(1, runnable.ainvoke.await_count)
        self.assertEqual(1, evidence.call_count)
        self.assertTrue(evidence.call_args.kwargs["streamed_partial"])

    async def test_transport_failure_is_not_retried_without_proof_of_no_send(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts = self._prompts(temporary_directory)
            runnable = SimpleNamespace(
                ainvoke=AsyncMock(side_effect=StreamChunkTimeoutError("stream idle"))
            )
            llm = SimpleNamespace(with_structured_output=MagicMock(return_value=runnable))
            with (
                patch(
                    "llm.runners.yaml_llm_runner.load_llm_config",
                    return_value=SimpleNamespace(
                        provider="openai", model="test-model",
                        structured_output_method="function_calling",
                    ),
                ),
                patch("llm.runners.yaml_llm_runner.build_llm_client", return_value=llm),
                patch(
                    "llm.runners.yaml_llm_runner.apply_cache_friendly_filters",
                    side_effect=lambda messages, provider: messages,
                ),
                patch("llm.runners.yaml_llm_runner.response_cache.load", return_value=None),
                patch("llm.runners.yaml_llm_runner._write_error_record") as evidence,
            ):
                with self.assertRaises(StreamChunkTimeoutError):
                    await run_yaml_rule(
                        prompts_dir=prompts,
                        rule_name="boundary",
                        user_values={"value": "input"},
                        response_model=_RetryResponse,
                    )

        self.assertEqual(1, runnable.ainvoke.await_count)
        self.assertEqual(1, evidence.call_count)

    async def test_semantic_failure_is_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts = self._prompts(temporary_directory)
            runnable = SimpleNamespace(
                ainvoke=AsyncMock(side_effect=ValueError("schema meaning invalid"))
            )
            llm = SimpleNamespace(with_structured_output=MagicMock(return_value=runnable))
            with (
                patch(
                    "llm.runners.yaml_llm_runner.load_llm_config",
                    return_value=SimpleNamespace(
                        provider="openai", model="test-model",
                        structured_output_method="function_calling",
                    ),
                ),
                patch("llm.runners.yaml_llm_runner.build_llm_client", return_value=llm),
                patch(
                    "llm.runners.yaml_llm_runner.apply_cache_friendly_filters",
                    side_effect=lambda messages, provider: messages,
                ),
                patch("llm.runners.yaml_llm_runner.response_cache.load", return_value=None),
                patch("llm.runners.yaml_llm_runner._write_error_record") as evidence,
            ):
                with self.assertRaisesRegex(ValueError, "schema meaning invalid"):
                    await run_yaml_rule(
                        prompts_dir=prompts,
                        rule_name="boundary",
                        user_values={"value": "input"},
                        response_model=_RetryResponse,
                    )

        self.assertEqual(1, runnable.ainvoke.await_count)
        self.assertEqual(1, evidence.call_count)

if __name__ == "__main__":
    unittest.main()
