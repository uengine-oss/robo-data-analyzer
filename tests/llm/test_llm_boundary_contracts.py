"""LLM config, fan-out, and filesystem failure-surface contracts."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain.agents.middleware.types import ModelResponse
from pydantic import BaseModel, ValidationError

from llm.agents.agent_output import CallWrapper, DdlTransform, TableAccessPattern
from llm.agents.code_inspection import SkillCatalogError, build_skill_catalog
from llm.agents.filesystem_tools import make_filesystem_tools
from llm import embedder
from llm.llm_client import build_llm_client
from llm.llm_config_loader import _build_llm_config, load_llm_config
from llm.llm_fanout import LlmFanoutResultCountError, partition_llm_results
from llm.middleware.cost_tracking import CostTrackingMiddleware
from llm.middleware.call_limit import ModelCallLimitMiddleware
from llm.middleware.todo_tracker import TodoTrackerMiddleware
from llm.middleware.tool_event_emitter import ToolEventEmitterMiddleware
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.prompt_values import _extract_target_and_sort
from llm.runners.structured_agent import run_structured_agent
from llm.prompt_logging import prompt_logger
from llm.usage.cost_budget import CostBudget, InvalidCostAmountError
from llm.usage.pricing import UnknownModelPriceError, compute_cost
from llm.usage.token_usage import (
    InvalidTokenUsageError,
    TokenUsageRecord,
    UnmeteredLlmCallError,
    track_usage,
)
from llm.usage.usage_jsonl_writer import (
    UsageRecordWriteError,
    append_usage_record,
    persist_usage_and_charge_budget,
)
from search.query.query_llm import _record_usage


class _BoundaryResponse(BaseModel):
    value: str


class LlmBoundaryContractTest(unittest.TestCase):
    def test_prompt_record_target_survives_runner_split(self) -> None:
        target_id, sort_key = _extract_target_and_sort(
            {"table_fqn": "db:RWIS.TABLE_A", "file_path": "a.sql", "start_line": 7}
        )

        self.assertEqual(target_id, "db:RWIS.TABLE_A")
        self.assertEqual(sort_key, "a.sql:7")

    def test_fanout_count_mismatch_is_rejected(self) -> None:
        with self.assertRaises(LlmFanoutResultCountError):
            partition_llm_results(
                ["first", "second"],
                ["only-result"],
                component="test",
                operation="partition",
            )

    def test_fanout_cancellation_is_never_dropped(self) -> None:
        cancellation = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            partition_llm_results(
                ["cancelled-item"],
                [cancellation],
                component="test",
                operation="partition",
                on_failure="drop",
            )

    def test_unknown_fanout_failure_policy_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            partition_llm_results(
                ["item"],
                ["result"],
                component="test",
                operation="partition",
                on_failure="typo",  # type: ignore[arg-type]
            )

    def test_embedding_result_count_must_match_input_count(self) -> None:
        provider = embedder.SETTINGS.embedding.provider
        with patch.dict(embedder._ENCODERS, {provider: lambda _texts: [[0.1], [0.2]]}):
            with self.assertRaises(embedder.EmbeddingError):
                embedder.encode_texts(["one input"])

    def test_config_name_cannot_escape_configs_directory(self) -> None:
        with self.assertRaises(RuntimeError):
            load_llm_config("../outside")

    def test_bundled_config_resolves_from_final_llm_tree(self) -> None:
        config = load_llm_config("qwen36_sglang_local")

        self.assertEqual(config.provider, "sglang")
        self.assertTrue(config.model)

    def test_quoted_false_is_not_misread_as_true(self) -> None:
        with self.assertRaises(RuntimeError):
            _build_llm_config(
                {
                    "provider": "openai",
                    "model": "test-model",
                    "api_base": "https://provider.invalid/v1",
                    "verify_ssl": "false",
                },
                "test-config",
            )

    def test_fractional_integer_config_is_rejected_instead_of_truncated(self) -> None:
        with self.assertRaises(RuntimeError):
            _build_llm_config(
                {
                    "provider": "openai",
                    "model": "test-model",
                    "api_base": "https://provider.invalid/v1",
                    "max_tokens": 1.5,
                },
                "test-config",
            )

    def test_unknown_reasoning_effort_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            _build_llm_config(
                {
                    "provider": "openai",
                    "model": "test-model",
                    "api_base": "https://provider.invalid/v1",
                    "reasoning_effort": "typo",
                },
                "test-config",
            )

    def test_nonfinite_sampling_config_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            _build_llm_config(
                {
                    "provider": "openai",
                    "model": "test-model",
                    "api_base": "https://provider.invalid/v1",
                    "sampling": {"temperature": float("nan")},
                },
                "test-config",
            )

    def test_zero_max_tokens_override_is_not_silently_replaced(self) -> None:
        config = _build_llm_config(
            {
                "provider": "openai",
                "model": "test-model",
                "api_base": "https://provider.invalid/v1",
            },
            "test-config",
        )
        settings = SimpleNamespace(
            analysis_llm=SimpleNamespace(config="test-config", api_key="test-key")
        )
        with (
            patch("llm.llm_client.load_llm_config", return_value=config),
            patch("llm.llm_client.SETTINGS", settings),
        ):
            with self.assertRaises(RuntimeError):
                build_llm_client(max_tokens=0)

    def test_agent_output_rejects_removed_and_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            TableAccessPattern.model_validate(
                {
                    "id": "pattern",
                    "call_regex": "(?P<op_token>SELECT).*(?P<entity>TABLE)",
                    "op_mappings": [{"token": "SELECT", "op": "SELECT"}],
                    "entity_source": "unused-contract",
                }
            )

    def test_agent_output_rejects_unsupported_dispatch_values(self) -> None:
        with self.assertRaises(ValidationError):
            DdlTransform(ddl_naming_rule="typo")  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            CallWrapper(wrapper="dispatch", callee_from="typo")  # type: ignore[arg-type]

    def test_skill_catalog_read_failure_is_not_hidden_as_blank_description(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            skills_dir = Path(temporary_directory)
            skill_dir = skills_dir / "empty-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("", encoding="utf-8")

            with self.assertRaises(SkillCatalogError):
                build_skill_catalog(skills_dir)

    def test_search_llm_usage_is_required(self) -> None:
        with self.assertRaises(UnmeteredLlmCallError):
            _record_usage({}, model="local", latency_ms=1)

    def test_usage_write_failure_is_explicit(self) -> None:
        usage = TokenUsageRecord(model="local", input_tokens=1, output_tokens=1)
        with tempfile.TemporaryDirectory() as temporary_directory:
            blocking_parent = Path(temporary_directory) / "not-a-directory"
            blocking_parent.write_text("file", encoding="utf-8")
            with self.assertRaises(UsageRecordWriteError):
                append_usage_record(
                    blocking_parent / "usage.jsonl", "test", usage, 0.0
                )

    def test_incurred_cost_is_charged_even_when_usage_write_fails(self) -> None:
        usage = TokenUsageRecord(model="local", input_tokens=1, output_tokens=1)
        budget = CostBudget(limit_usd=1.0)
        with tempfile.TemporaryDirectory() as temporary_directory:
            blocking_parent = Path(temporary_directory) / "not-a-directory"
            blocking_parent.write_text("file", encoding="utf-8")
            with self.assertRaises(UsageRecordWriteError):
                persist_usage_and_charge_budget(
                    blocking_parent / "usage.jsonl",
                    "test",
                    usage,
                    cost_usd=0.25,
                    cost_budget=budget,
                )

        self.assertEqual(budget.spent_usd, 0.25)

    def test_optional_agent_diagnostics_do_not_replace_success_with_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            blocking_parent = Path(temporary_directory) / "not-a-directory"
            blocking_parent.write_text("file", encoding="utf-8")
            diagnostic_path = blocking_parent / "events.jsonl"

            todo_middleware = TodoTrackerMiddleware("test", diagnostic_path)
            state = {"todos": [{"content": "inspect", "status": "in_progress"}]}
            self.assertIs(todo_middleware.after_model(state, None), state)

            tool_middleware = ToolEventEmitterMiddleware("test", diagnostic_path)
            tool_middleware._emit(
                "tool_start",
                "grep",
                {"pattern": "needle"},
                elapsed_ms=None,
                error=None,
                result_stats=None,
                result_content=None,
            )

    def test_nonpositive_model_call_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ModelCallLimitMiddleware("test", max_calls=0)

    def test_unreadable_utf8_file_is_reported_by_grep(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            (source_root / "binary.dat").write_bytes(b"\xff\xfe\x00")
            grep_tool = make_filesystem_tools(source_root)["grep"]

            output = grep_tool.invoke({
                "pattern": "needle",
                "path_glob": "**/*",
                "base": ".",
            })

        self.assertIn("NO_MATCHES", output)
        self.assertIn("READ_SKIPPED: count=1", output)
        self.assertIn("UnicodeDecodeError", output)

    def test_usage_metadata_is_required_for_cost_accounting(self) -> None:
        response_without_usage = SimpleNamespace(usage_metadata=None)

        with self.assertRaises(UnmeteredLlmCallError):
            track_usage(response_without_usage, model="test-model")

    def test_unknown_model_price_is_rejected(self) -> None:
        usage = TokenUsageRecord(
            model="definitely-unregistered-paid-model",
            input_tokens=1_000,
            output_tokens=100,
        )

        with self.assertRaises(UnknownModelPriceError):
            compute_cost(usage)

    def test_unknown_model_cannot_inherit_free_price_by_substring(self) -> None:
        usage = TokenUsageRecord(
            model="paid-localized-model",
            input_tokens=1_000,
            output_tokens=100,
        )

        with self.assertRaises(UnknownModelPriceError):
            compute_cost(usage)

    def test_explicit_zero_cost_model_remains_valid(self) -> None:
        usage = TokenUsageRecord(
            model="local",
            input_tokens=1_000,
            output_tokens=100,
        )

        self.assertEqual(compute_cost(usage), 0.0)

    def test_negative_cost_cannot_reduce_spent_budget(self) -> None:
        budget = CostBudget(limit_usd=1.0, spent_usd=0.5)

        with self.assertRaises(InvalidCostAmountError):
            budget.add_sync(-0.25)

        self.assertEqual(budget.spent_usd, 0.5)

    def test_negative_provider_token_count_is_rejected(self) -> None:
        response = SimpleNamespace(
            usage_metadata={"input_tokens": 100, "output_tokens": -1}
        )

        with self.assertRaises(InvalidTokenUsageError):
            track_usage(response, model="local")

    def test_direct_cost_record_cannot_bypass_token_validation(self) -> None:
        invalid_usage = TokenUsageRecord(
            model="local",
            input_tokens=100,
            output_tokens=-1,
        )

        with self.assertRaises(InvalidTokenUsageError):
            compute_cost(invalid_usage)


class AsyncLlmBoundaryContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_recording_failure_does_not_replace_agent_success(self) -> None:
        agent = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value={"structured_response": _BoundaryResponse(value="ok")}
            )
        )
        with (
            patch("llm.runners.structured_agent.build_llm_client", return_value=object()),
            patch("llm.runners.structured_agent.build_standard_stack", return_value=[]),
            patch("llm.runners.structured_agent.create_agent", return_value=agent),
            patch(
                "llm.runners.structured_agent.begin_agent_invoke",
                side_effect=OSError("diagnostic disk unavailable"),
            ),
            patch("llm.runners.structured_agent.end_agent_invoke") as end_recording,
        ):
            result = await run_structured_agent(
                agent_name="boundary",
                invoke_id="run",
                system_prompt="submit when done",
                tools=[],
                response_format=_BoundaryResponse,
                user_message="inspect",
                max_calls=1,
                run_dir=Path("unused"),
                cost_budget=CostBudget(limit_usd=1.0),
            )

        self.assertEqual(result.value, "ok")
        end_recording.assert_not_called()

    async def test_prompt_context_is_cleared_when_final_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = prompt_logger._AgentInvokeState(
                agent_name="test",
                invoke_id="run",
                out_path=Path(temporary_directory) / "agent.json",
                system_prompt="system",
            )
            prompt_logger._invoke_var.set(state)
            with patch.object(prompt_logger, "_write_json", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    prompt_logger.end_agent_invoke()

        self.assertIsNone(prompt_logger._invoke_var.get())

    async def test_agent_response_without_ai_message_is_rejected(self) -> None:
        middleware = CostTrackingMiddleware(
            usage_log_path=Path("unused.jsonl"),
            cost_budget=CostBudget(limit_usd=1.0),
            agent_name="boundary-test",
        )

        async def handler(_request: object) -> ModelResponse:
            return ModelResponse(result=[])

        with self.assertRaises(UnmeteredLlmCallError):
            await middleware.awrap_model_call(object(), handler)

    async def test_yaml_response_without_raw_ai_message_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts_dir = Path(temporary_directory)
            (prompts_dir / "boundary.yaml").write_text(
                "system: test system\nuser_template: '{value}'\nmodel: test-model\n",
                encoding="utf-8",
            )
            structured_model = SimpleNamespace(
                ainvoke=AsyncMock(
                    return_value={
                        "raw": None,
                        "parsed": _BoundaryResponse(value="parsed"),
                    }
                )
            )
            llm_client = SimpleNamespace(
                with_structured_output=lambda *_args, **_kwargs: structured_model
            )

            with (
                patch(
                    "llm.runners.yaml_llm_runner.load_llm_config",
                    return_value=SimpleNamespace(provider="openai"),
                ),
                patch(
                    "llm.runners.yaml_llm_runner.build_llm_client",
                    return_value=llm_client,
                ),
                patch(
                    "llm.runners.yaml_llm_runner.apply_cache_friendly_filters",
                    side_effect=lambda messages, provider: messages,
                ),
                patch("llm.runners.yaml_llm_runner._write_error_record") as error_record,
            ):
                with self.assertRaises(UnmeteredLlmCallError):
                    await run_yaml_rule(
                        prompts_dir=prompts_dir,
                        rule_name="boundary",
                        user_values={"value": "input"},
                        response_model=_BoundaryResponse,
                    )

        error_record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
