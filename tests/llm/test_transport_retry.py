from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel

from llm.runners.model_output_error import ModelOutputError
from llm.runners.transport_retry import is_transient_transport_error
from llm.runners.yaml_llm_runner import run_yaml_rule


class RemoteProtocolError(Exception):
    pass


class LengthFinishReasonError(Exception):
    pass


class RepetitionLoopAborted(Exception):
    pass


class StreamChunkTimeoutError(Exception):
    pass


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _RetryResponse(BaseModel):
    value: str


class TransportRetryPolicyTest(unittest.TestCase):
    def test_transient_network_and_server_failures_retry(self):
        self.assertTrue(is_transient_transport_error(RemoteProtocolError()))
        self.assertTrue(is_transient_transport_error(StreamChunkTimeoutError()))
        self.assertTrue(is_transient_transport_error(StatusError(429)))
        self.assertTrue(is_transient_transport_error(StatusError(503)))

    def test_semantic_and_non_transient_failures_do_not_retry(self):
        self.assertFalse(is_transient_transport_error(LengthFinishReasonError()))
        self.assertFalse(is_transient_transport_error(RepetitionLoopAborted()))
        self.assertFalse(is_transient_transport_error(ValueError("schema")))
        self.assertFalse(is_transient_transport_error(StatusError(401)))


class TransportRetryRunnerTest(unittest.IsolatedAsyncioTestCase):
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
                patch("llm.runners.yaml_llm_runner.asyncio.sleep", new=AsyncMock()) as sleep,
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
        self.assertEqual(0, sleep.await_count)
        self.assertTrue(evidence.call_args.kwargs["streamed_partial"])

    async def test_stream_idle_before_first_chunk_retries_transport(self) -> None:
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
                patch("llm.runners.yaml_llm_runner.asyncio.sleep", new=AsyncMock()) as sleep,
            ):
                with self.assertRaises(StreamChunkTimeoutError):
                    await run_yaml_rule(
                        prompts_dir=prompts,
                        rule_name="boundary",
                        user_values={"value": "input"},
                        response_model=_RetryResponse,
                    )

        self.assertEqual(3, runnable.ainvoke.await_count)
        self.assertEqual(3, evidence.call_count)
        self.assertEqual(2, sleep.await_count)

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
                patch("llm.runners.yaml_llm_runner.asyncio.sleep", new=AsyncMock()) as sleep,
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
        self.assertEqual(0, sleep.await_count)

    async def test_generation_deadline_is_quarantined_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts = Path(temporary_directory)
            (prompts / "boundary.yaml").write_text(
                "system: test\nuser_template: '{value}'\nmodel: test-model\n"
                "wall_timeout_seconds: 0.01\n",
                encoding="utf-8",
            )

            async def never_finishes(_messages, config=None):
                await asyncio.Event().wait()

            runnable = SimpleNamespace(ainvoke=AsyncMock(side_effect=never_finishes))
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
                patch("llm.runners.yaml_llm_runner.asyncio.sleep", new=AsyncMock()) as sleep,
            ):
                with self.assertRaises(ModelOutputError) as caught:
                    await run_yaml_rule(
                        prompts_dir=prompts,
                        rule_name="boundary",
                        user_values={"value": "input"},
                        response_model=_RetryResponse,
                    )

        self.assertEqual("deadline", caught.exception.kind)
        self.assertEqual(1, runnable.ainvoke.await_count)
        self.assertEqual(1, evidence.call_count)
        self.assertEqual(0, sleep.await_count)


if __name__ == "__main__":
    unittest.main()
