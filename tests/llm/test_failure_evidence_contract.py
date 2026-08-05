"""spec 116 0단계 — 실패 시에도 raw 증거를 지우지 않는 계약 (반증·오류주입 테스트).

검증 실패(parsing_error)·반복 폭주 중단(RepetitionLoopAborted)을 주입해
- 실패 사유가 record metadata 와 raise 메시지에 구체적으로 남고
- partial raw 가 에러 record 의 response 로 남는지
를 검증한다. 실패가 성공 모양(parsed=None, 사유 없음)으로 남으면 계약 위반이다.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage

from llm.repetition_guard import RepetitionLoopAborted
from llm.runners.model_output_error import ModelOutputError
from llm.runners.invocation import _extract_partial_content, _write_error_record
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.usage.token_usage import TokenUsageRecord
from pydantic import BaseModel


class _Response(BaseModel):
    value: str


class ParsingErrorEvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_validation_failure_keeps_raw_and_surfaces_reason(self) -> None:
        raw_msg = AIMessage(content='{"value": "truncated')
        injected = ValueError("unfinished Rule statement: unbalanced delimiter")

        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts_dir = Path(temporary_directory)
            (prompts_dir / "boundary.yaml").write_text(
                "system: test system\nuser_template: '{value}'\nmodel: test-model\n",
                encoding="utf-8",
            )
            structured_model = SimpleNamespace(
                ainvoke=AsyncMock(
                    return_value={
                        "raw": raw_msg,
                        "parsed": None,
                        "parsing_error": injected,
                    }
                )
            )
            llm_client = SimpleNamespace(
                with_structured_output=lambda *_args, **_kwargs: structured_model
            )

            with (
                patch(
                    "llm.runners.yaml_llm_runner.load_llm_config",
                    return_value=SimpleNamespace(
                        provider="openai", model="cfg-model",
                        structured_output_method="function_calling",
                    ),
                ),
                patch(
                    "llm.runners.yaml_llm_runner.build_llm_client",
                    return_value=llm_client,
                ),
                patch(
                    "llm.runners.yaml_llm_runner.apply_cache_friendly_filters",
                    side_effect=lambda messages, provider: messages,
                ),
                patch(
                    "llm.runners.yaml_llm_runner.response_cache.load",
                    return_value=None,
                ),
                patch(
                    "llm.runners.yaml_llm_runner.track_usage",
                    return_value=TokenUsageRecord(
                        model="test-model", input_tokens=1, output_tokens=1, latency_ms=1
                    ),
                ),
                patch("llm.runners.yaml_llm_runner.compute_cost", return_value=0.0),
                patch(
                    "llm.runners.yaml_llm_runner.persist_usage_and_charge_budget"
                ),
                patch(
                    "llm.runners.yaml_llm_runner._resolve_usage_log_path",
                    return_value=prompts_dir / "usage.jsonl",
                ),
                patch("llm.runners.yaml_llm_runner.write_rule_record") as record,
            ):
                # 실패 사유가 raise 메시지에 구체적으로 실린다 — 재시도 계층이
                # "무엇이 틀렸는지"를 받는 유일한 통로다.
                with self.assertRaisesRegex(RuntimeError, "unbalanced delimiter"):
                    await run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="boundary",
                        user_values={"value": "input"},
                        response_model=_Response,
                    )

        record.assert_called_once()
        kwargs = record.call_args.kwargs
        # raw 전문이 record 에 남는다 — 검증 실패가 raw 를 지우면 안 된다.
        self.assertIsNotNone(kwargs["response"]["raw"])
        self.assertIsNone(kwargs["response"]["parsed"])
        # 실패 사유가 metadata 에 남아 record 가 성공 모양이 되지 않는다.
        self.assertIn("unbalanced delimiter", kwargs["metadata"]["parsing_error"])
        self.assertIn("ValueError", kwargs["metadata"]["parsing_error"])


class ProviderSchemaBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def _invoke(self, parsed: object, *, raw_content: str | None = None):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        prompts_dir = Path(temporary_directory.name)
        (prompts_dir / "boundary.yaml").write_text(
            "system: test system\nuser_template: '{value}'\nmodel: test-model\n",
            encoding="utf-8",
        )
        raw_msg = AIMessage(
            content=json.dumps(parsed) if raw_content is None else raw_content
        )
        structured_model = SimpleNamespace(
            ainvoke=AsyncMock(return_value={
                "raw": raw_msg,
                "parsed": parsed,
                "parsing_error": None,
            })
        )
        with_structured_output = MagicMock(return_value=structured_model)
        llm_client = SimpleNamespace(with_structured_output=with_structured_output)
        patches = (
            patch(
                "llm.runners.yaml_llm_runner.load_llm_config",
                return_value=SimpleNamespace(
                    provider="openai", model="cfg-model",
                    structured_output_method="function_calling",
                ),
            ),
            patch(
                "llm.runners.yaml_llm_runner.build_llm_client",
                return_value=llm_client,
            ),
            patch(
                "llm.runners.yaml_llm_runner.apply_cache_friendly_filters",
                side_effect=lambda messages, provider: messages,
            ),
            patch("llm.runners.yaml_llm_runner.response_cache.load", return_value=None),
            patch("llm.runners.yaml_llm_runner.response_cache.store"),
            patch(
                "llm.runners.yaml_llm_runner.track_usage",
                return_value=TokenUsageRecord(
                    model="test-model", input_tokens=1, output_tokens=1, latency_ms=1
                ),
            ),
            patch("llm.runners.yaml_llm_runner.compute_cost", return_value=0.0),
            patch("llm.runners.yaml_llm_runner.persist_usage_and_charge_budget"),
            patch(
                "llm.runners.yaml_llm_runner._resolve_usage_log_path",
                return_value=prompts_dir / "usage.jsonl",
            ),
            patch("llm.runners.yaml_llm_runner.write_rule_record"),
        )
        entered = [item.start() for item in patches]
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
        record = entered[-1]
        return prompts_dir, with_structured_output, record

    async def test_provider_receives_canonical_json_schema_and_internal_model_returns(self) -> None:
        prompts_dir, boundary, _record = await self._invoke({"value": "parsed"})

        result = await run_yaml_rule(
            prompts_dir=prompts_dir,
            rule_name="boundary",
            user_values={"value": "input"},
            response_model=_Response,
        )

        schema_arg = boundary.call_args.args[0]
        self.assertIsInstance(schema_arg, dict)
        self.assertEqual(_Response.model_json_schema(), schema_arg)
        self.assertTrue(boundary.call_args.kwargs["include_raw"])
        self.assertEqual("function_calling", boundary.call_args.kwargs["method"])
        self.assertTrue(boundary.call_args.kwargs["strict"])
        self.assertIsInstance(result, _Response)
        self.assertEqual("parsed", result.value)

    async def test_internal_validation_failure_keeps_raw_and_fails_closed(self) -> None:
        prompts_dir, _boundary, record = await self._invoke({})

        with self.assertRaisesRegex(ModelOutputError, "Field required") as caught:
            await run_yaml_rule(
                prompts_dir=prompts_dir,
                rule_name="boundary",
                user_values={"value": "input"},
                response_model=_Response,
            )
        self.assertEqual("schema", caught.exception.kind)

        evidence = record.call_args.kwargs
        self.assertIsNotNone(evidence["response"]["raw"])
        self.assertIsNone(evidence["response"]["parsed"])
        self.assertIn("ValidationError", evidence["metadata"]["parsing_error"])
        self.assertEqual(
            "function_calling", evidence["metadata"]["structured_output_method"]
        )

    async def test_partially_repaired_provider_json_is_rejected(self) -> None:
        prompts_dir, _boundary, record = await self._invoke(
            {"value": "truncated"}, raw_content='{"value": "truncated'
        )

        with self.assertRaisesRegex(ModelOutputError, "Unterminated string") as caught:
            await run_yaml_rule(
                prompts_dir=prompts_dir,
                rule_name="boundary",
                user_values={"value": "input"},
                response_model=_Response,
            )
        self.assertEqual("schema", caught.exception.kind)

        evidence = record.call_args.kwargs
        self.assertIsNotNone(evidence["response"]["raw"])
        self.assertIsNone(evidence["response"]["parsed"])
        self.assertIn("JSONDecodeError", evidence["metadata"]["parsing_error"])


class RealChainJsonSchemaBoundaryProof(unittest.IsolatedAsyncioTestCase):
    """MockTransport 실체인으로 provider JSON Schema 경계와 raw 보존을 고정한다."""

    async def _invoke(self, content: str, model_cls):
        import httpx
        from langchain_openai import ChatOpenAI

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "cmpl-1", "object": "chat.completion", "created": 1,
                "model": "m",
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2},
            })

        llm = ChatOpenAI(
            model="m", openai_api_key="k",
            http_async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ),
        )
        runnable = llm.with_structured_output(
            model_cls.model_json_schema(), include_raw=True
        )
        return await runnable.ainvoke([("user", "probe")])

    async def test_complete_json_returns_dict_and_raw(self) -> None:
        result = await self._invoke('{"value": "x"}', _Response)
        self.assertEqual({"value": "x"}, result["parsed"])
        self.assertIsNone(result["parsing_error"])
        self.assertEqual('{"value": "x"}', result["raw"].content)

    async def test_truncated_json_raw_is_preserved_for_runner_guard(self) -> None:
        raw = '{"value": "trunc'
        result = await self._invoke(raw, _Response)
        self.assertEqual(raw, result["raw"].content)


class PartialContentEvidenceTest(unittest.TestCase):
    def test_repetition_abort_partial_reaches_error_record(self) -> None:
        error = RepetitionLoopAborted(
            "rule:boundary", 1, 4096, partial_text='{"rules": [    '
        )
        with patch("llm.runners.invocation.write_rule_record") as record:
            _write_error_record(
                rule_name="boundary",
                system_prompt="sys",
                user_content="user",
                user_values={"id": "code:x"},
                error=error,
                elapsed_ms=5,
                structured_output_method="json_schema",
            )
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["response"], '{"rules": [    ')
        self.assertEqual(kwargs["metadata"]["partial_chars"], len('{"rules": [    '))
        self.assertEqual("json_schema", kwargs["metadata"]["structured_output_method"])

    def test_partial_text_attribute_takes_precedence(self) -> None:
        error = RepetitionLoopAborted("rule:x", 1, 10, partial_text="tail")
        self.assertEqual(_extract_partial_content(error), "tail")

    def test_stream_callback_tail_fills_sdk_timeout_evidence(self) -> None:
        with patch("llm.runners.invocation.write_rule_record") as record:
            _write_error_record(
                rule_name="boundary",
                system_prompt="sys",
                user_content="user",
                user_values={"id": "code:x"},
                error=TimeoutError("stream idle"),
                elapsed_ms=120_000,
                streamed_partial='{"value":"partial',
            )
        kwargs = record.call_args.kwargs
        self.assertEqual('{"value":"partial', kwargs["response"])
        self.assertEqual(len('{"value":"partial'), kwargs["metadata"]["partial_chars"])

    def test_exception_owned_partial_precedes_stream_callback_tail(self) -> None:
        error = RepetitionLoopAborted("rule:x", 1, 10, partial_text="owned")
        with patch("llm.runners.invocation.write_rule_record") as record:
            _write_error_record(
                rule_name="boundary",
                system_prompt="sys",
                user_content="user",
                user_values={"id": "code:x"},
                error=error,
                elapsed_ms=5,
                streamed_partial="callback",
            )
        self.assertEqual("owned", record.call_args.kwargs["response"])

    def test_length_finish_completion_path_still_works(self) -> None:
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="cut body"))]
        )
        error = SimpleNamespace(completion=completion)
        self.assertEqual(_extract_partial_content(error), "cut body")

    def test_errors_without_evidence_return_empty(self) -> None:
        self.assertEqual(_extract_partial_content(ValueError("boom")), "")

    def test_validation_input_extraction_failure_is_visible(self) -> None:
        error = SimpleNamespace(errors=lambda: (_ for _ in ()).throw(RuntimeError("bad evidence")))
        with patch("llm.runners.invocation.log_warning") as warning:
            self.assertEqual("", _extract_partial_content(error))
        warning.assert_called_once()
        self.assertEqual("extract_validation_input_failed", warning.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
