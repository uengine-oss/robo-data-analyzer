"""Agent contracts plus the LLM infrastructure they execute through."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field, ValidationError

from agents.contracts import CallWrapper, DdlTransform, TableAccessPattern
from agents.tools.inspection import (
    SkillCatalogError,
    build_skill_catalog,
    make_list_code_nodes,
)
from agents.tools.filesystem import make_filesystem_tools
from graph.schema.graph_nodes import CodeNode
from llm import embedder
from llm.client import build_llm_client
from llm.configs.loader import _build_llm_config, load_llm_config
from agents.middleware.cost_tracking import CostTrackingMiddleware
from agents.middleware.tool_events import ToolEventEmitterMiddleware
from agents.scan import _build_user_message as build_scan_user_message
from llm.runners.yaml_llm_runner import run_yaml_rule
from llm.runners.prompt_values import _extract_target_and_sort
from agents.runtime import (
    StructuredResponseMissingError,
    StructuredResponseStateError,
    StructuredResponseValidationError,
    _wrap_pydantic_as_submit_tool,
    run_structured_agent,
)
from llm import tracing
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
from pipeline.execution.run_output_context import (
    reset_response_locale,
    set_response_locale,
)


class _BoundaryResponse(BaseModel):
    value: str


class _DefaultedBoundaryResponse(BaseModel):
    value: str
    optional_items: list[str] = Field(default_factory=list)


class LlmBoundaryContractTest(unittest.TestCase):
    def test_scan_prompt_hides_bound_root_and_keeps_compact_complete_inventory(self) -> None:
        inventory = {
            "extension_counts": {f"x{index:02d}": 1 for index in range(25)},
            "manifest_paths": ["project.manifest"],
            "scanner_category_file_counts": {"Source": 75},
        }

        message = build_scan_user_message(inventory, [])

        self.assertNotIn("project_root", message)
        self.assertNotIn("analysis-root", message)
        self.assertIn('"x24":1', message)
        self.assertIn('"Source":75', message)
        self.assertNotIn("manifest_content", message)
        self.assertIn("ScanProposal 을 출력하라", message)
        self.assertNotIn("ScanResult 를 출력하라", message)

        prompt = (
            Path(__file__).resolve().parents[2] / "agents" / "prompts" / "scan.md"
        ).read_text(encoding="utf-8")
        self.assertIn("경로는 프로젝트 상대경로", prompt)
        self.assertNotIn("user message 의 `project_root`", prompt)

    def test_agent_stack_has_no_lossy_context_rewrite_or_summary(self) -> None:
        root = Path(__file__).resolve().parents[2]
        stack_source = (root / "agents" / "middleware" / "stack.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "ClearToolUsesEdit",
            "ContextEditingMiddleware",
            "SummarizationMiddleware",
            "context_editing_trigger_tokens",
            "_TOOL_RESULT_KEEP_LAST",
            "TodoListMiddleware",
            "TodoTrackerMiddleware",
            "write_todos",
            "PatchToolCallsMiddleware",
            "deepagents",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, stack_source)
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("deepagents", requirements)
        settings_source = (root / "shared" / "config" / "settings.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("context_editing_trigger_tokens", settings_source)
        self.assertNotIn(
            "ROBO_AGENT_CONTEXT_EDITING_TRIGGER_TOKENS",
            (root / ".env.example").read_text(encoding="utf-8"),
        )

        # Scan Agent prose is metadata, never authoritative input to another LLM.
        for relative_path in (
            "agents/prompts/residual.yaml",
            "pipeline/semantic_tasks/summary/prompts/framework/_common.yaml",
            "pipeline/semantic_tasks/summary/prompts/dbms/_common.yaml",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertNotIn(
                    "{codebase_notes}",
                    (root / relative_path).read_text(encoding="utf-8"),
                )
        for relative_path in (
            "agents/residual.py",
            "pipeline/semantic_tasks/step_entry.py",
            "pipeline/semantic_tasks/table/prompt_values.py",
            "pipeline/semantic_tasks/three_split_llm_fanout.py",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertNotIn(
                    '"codebase_notes":',
                    (root / relative_path).read_text(encoding="utf-8"),
                )

    def test_agent_transcript_preserves_first_human_then_only_new_tool_message(self) -> None:
        usage = SimpleNamespace(
            input_tokens=10, output_tokens=2, cached_tokens=0,
        )
        first_human = HumanMessage(content="initial structured input")
        first_ai = AIMessage(content="", tool_calls=[])
        tool_result = ToolMessage(content="tool evidence", tool_call_id="call-1")

        with patch.object(tracing, "append_agent_turn") as append:
            offset = tracing.append_conversation_turn(
                request_messages=[first_human],
                response_message=first_ai,
                last_message_count=0,
                model="m",
                latency_ms=1,
                cost_usd=0.0,
                usage=usage,
            )
            next_offset = tracing.append_conversation_turn(
                request_messages=[first_human, first_ai, tool_result],
                response_message=AIMessage(content="done"),
                last_message_count=offset,
                model="m",
                latency_ms=1,
                cost_usd=0.0,
                usage=usage,
            )

        self.assertEqual(2, offset)
        self.assertEqual(4, next_offset)
        first_record = append.call_args_list[0].kwargs["user_content"]
        second_record = append.call_args_list[1].kwargs["user_content"]
        self.assertIn("initial structured input", first_record)
        self.assertNotIn("initial structured input", second_record)
        self.assertIn("tool evidence", second_record)

    def test_submit_tool_preserves_default_factory_as_optional_schema_field(self) -> None:
        submit = _wrap_pydantic_as_submit_tool(
            _DefaultedBoundaryResponse, tool_name="submit_boundary",
        )

        schema = submit.args_schema.model_json_schema()

        self.assertIn("value", schema["required"])
        self.assertNotIn("optional_items", schema["required"])
        self.assertEqual([], schema["properties"]["optional_items"]["default"])

    def test_prompt_record_target_survives_runner_split(self) -> None:
        target_id, sort_key = _extract_target_and_sort(
            {"table_fqn": "db:RWIS.TABLE_A", "file_path": "a.sql", "start_line": 7}
        )

        self.assertEqual(target_id, "db:RWIS.TABLE_A")
        self.assertEqual(sort_key, "a.sql:7")

    def test_component_and_glossary_calls_have_traceable_targets(self) -> None:
        self.assertEqual(
            ("COMPONENT:code:first", "COMPONENT:code:first"),
            _extract_target_and_sort({"component_id": "COMPONENT:code:first"}),
        )
        self.assertEqual(
            ("업무용어", "업무용어"),
            _extract_target_and_sort({"term": "업무용어"}),
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
        self.assertEqual("json_schema", config.structured_output_method)

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

    def test_unknown_structured_output_method_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            _build_llm_config(
                {
                    "provider": "openai",
                    "model": "test-model",
                    "api_base": "https://provider.invalid/v1",
                    "structured_output_method": "maybe",
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
            patch("llm.client.load_llm_config", return_value=config),
            patch("llm.client.SETTINGS", settings),
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

    def test_filesystem_search_pages_are_complete_without_silent_caps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            for index in range(205):
                (source_root / f"f{index:03}.txt").write_text(
                    f"needle {index}\n", encoding="utf-8",
                )
            tools = make_filesystem_tools(source_root)

            first_glob = tools["glob_search"].invoke({
                "pattern": "*.txt", "base": ".", "offset": 0,
            })
            last_glob = tools["glob_search"].invoke({
                "pattern": "*.txt", "base": ".", "offset": 200,
            })
            first_grep = tools["grep"].invoke({
                "pattern": "needle", "path_glob": "*.txt", "base": ".",
                "offset": 0,
            })
            last_grep = tools["grep"].invoke({
                "pattern": "needle", "path_glob": "*.txt", "base": ".",
                "offset": 160,
            })

        self.assertIn("total=205 next_offset=200", first_glob)
        self.assertIn("total=205 next_offset=null", last_glob)
        self.assertIn("total=205 next_offset=80", first_grep)
        self.assertIn("total=205 next_offset=null", last_grep)
        for page in (first_glob, last_glob, first_grep, last_grep):
            self.assertNotIn("returned=", page)
            self.assertNotIn("offset=0", page)
        self.assertNotIn(str(source_root), first_glob)
        self.assertNotIn(str(source_root), first_grep)
        self.assertIn("f000.txt", first_glob)
        self.assertIn("f000.txt:1:needle 0", first_grep)

    def test_relative_glob_path_opens_the_matching_code_node_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            source = source_root / "src" / "sample.c"
            source.parent.mkdir()
            source.write_text("void sample(void) {}\n", encoding="utf-8")
            nodes = [CodeNode(
                id="fn:sample", name="sample", signature="sample()",
                file_path="src/sample.c", start_line=1, end_line=1,
                label="FUNCTION",
            )]
            tools = make_filesystem_tools(source_root)
            listing = make_list_code_nodes(nodes, project_root=source_root)

            globbed = tools["glob_search"].invoke({
                "pattern": "**/*.c", "base": ".",
            })
            result = json.loads(listing.invoke({"file": "src/sample.c"}))

        self.assertIn("src/sample.c", globbed)
        self.assertNotIn(str(source_root), globbed)
        self.assertEqual("sample", result["items"][0]["name"])

    def test_source_page_headers_keep_closure_not_call_echoes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            source = source_root / "sample.c"
            source.write_text(
                "\n".join(f"line {index}" for index in range(1, 121)) + "\n",
                encoding="utf-8",
            )
            tools = make_filesystem_tools(source_root)
            first = tools["read_file"].invoke({
                "path": "sample.c", "start_line": 1,
            })
            requested = tools["read_file_range"].invoke({
                "path": "sample.c", "start_line": 1, "end_line": 120,
            })

        self.assertIn("total_lines=120 next_start_line=101", first)
        self.assertIn("next_start_line=101", requested)
        self.assertNotIn("PAGE start_line=", first)
        self.assertNotIn("returned=", first + requested)
        self.assertNotIn("requested=", requested)

    def test_partial_upload_tools_cannot_see_or_read_unselected_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory)
            selected = source_root / "selected.c"
            outside = source_root / "outside.c"
            selected.write_text("selected needle\n", encoding="utf-8")
            outside.write_text("outside needle\n", encoding="utf-8")
            tools = make_filesystem_tools(
                source_root,
                allowed_files=(selected,),
            )

            listing = tools["ls"].invoke({"path": "."})
            globbed = tools["glob_search"].invoke({
                "pattern": "*.c", "base": ".",
            })
            grepped = tools["grep"].invoke({
                "pattern": "needle", "path_glob": "*.c", "base": ".",
            })
            outside_read = tools["read_file"].invoke({"path": "outside.c"})

        self.assertIn("selected.c", listing)
        self.assertNotIn("outside.c", listing)
        self.assertIn("selected.c", globbed)
        self.assertNotIn("outside.c", globbed)
        self.assertIn("selected needle", grepped)
        self.assertNotIn("outside needle", grepped)
        self.assertEqual("NOT_SELECTED: outside.c", outside_read)

    def test_code_node_listing_exposes_every_page(self) -> None:
        nodes = [
            CodeNode(
                id=f"fn:{index}", name=f"fn_{index}", signature=f"fn_{index}()",
                file_path="sample.c", start_line=index + 1, end_line=index + 1,
                label="FUNCTION",
            )
            for index in range(55)
        ]
        listing = make_list_code_nodes(nodes)
        first = json.loads(listing.invoke({"file": "sample.c", "offset": 0}))
        second = json.loads(listing.invoke({"file": "sample.c", "offset": 50}))

        self.assertEqual((50, 55, 50), (
            len(first["items"]), first["total"], first["next_offset"],
        ))
        self.assertEqual((5, 55, None), (
            len(second["items"]), second["total"], second["next_offset"],
        ))
        self.assertNotIn("offset", first)
        self.assertNotIn("returned", first)

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

    def test_sync_budget_charges_are_thread_safe(self) -> None:
        budget = CostBudget(limit_usd=10.0)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _index: budget.add_sync(0.001), range(1_000)))

        self.assertAlmostEqual(budget.spent_usd, 1.0, places=10)

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
    async def asyncSetUp(self) -> None:
        self._response_locale_token = set_response_locale("ko")

    async def asyncTearDown(self) -> None:
        reset_response_locale(self._response_locale_token)

    async def test_submit_tool_validation_error_is_not_reported_as_missing_call(self) -> None:
        invalid_submit = AIMessage(
            content="",
            tool_calls=[{
                "name": "submit_boundary",
                "args": {},
                "id": "call-1",
                "type": "tool_call",
            }],
        )
        agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": [invalid_submit]}))
        with (
            patch("agents.runtime.build_llm_client", return_value=object()),
            patch("agents.runtime.build_standard_stack", return_value=[]),
            patch("agents.runtime.create_agent", return_value=agent),
            patch("agents.runtime.begin_agent_invoke"),
            patch("agents.runtime.end_agent_invoke"),
        ):
            with self.assertRaisesRegex(StructuredResponseValidationError, "value"):
                await run_structured_agent(
                    agent_name="boundary",
                    invoke_id="run",
                    system_prompt="submit when done",
                    tools=[],
                    response_format=_BoundaryResponse,
                    user_message="inspect",
                    run_dir=Path("unused"),
                    cost_budget=CostBudget(limit_usd=1.0),
                )

    async def test_valid_submit_without_state_is_reported_as_wiring_error(self) -> None:
        valid_submit = AIMessage(
            content="",
            tool_calls=[{
                "name": "submit_boundary",
                "args": {"value": "ok"},
                "id": "call-1",
                "type": "tool_call",
            }],
        )
        agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": [valid_submit]}))
        with (
            patch("agents.runtime.build_llm_client", return_value=object()),
            patch("agents.runtime.build_standard_stack", return_value=[]),
            patch("agents.runtime.create_agent", return_value=agent),
            patch("agents.runtime.begin_agent_invoke"),
            patch("agents.runtime.end_agent_invoke"),
        ):
            with self.assertRaises(StructuredResponseStateError):
                await run_structured_agent(
                    agent_name="boundary",
                    invoke_id="run",
                    system_prompt="submit when done",
                    tools=[],
                    response_format=_BoundaryResponse,
                    user_message="inspect",
                    run_dir=Path("unused"),
                    cost_budget=CostBudget(limit_usd=1.0),
                )

    async def test_tool_error_status_is_emitted_as_error_not_success(self) -> None:
        middleware = ToolEventEmitterMiddleware("boundary", Path("unused"))
        request = SimpleNamespace(
            tool_call={"name": "submit_boundary", "args": {}, "id": "call-1"},
        )
        error_result = ToolMessage(
            "schema invalid", name="submit_boundary", tool_call_id="call-1", status="error",
        )
        with patch.object(middleware, "_emit") as emit:
            returned = await middleware.awrap_tool_call(
                request, AsyncMock(return_value=error_result),
            )

        self.assertIs(returned, error_result)
        self.assertEqual("tool_error", emit.call_args.args[0])

    async def test_prompt_recording_failure_prevents_untraceable_agent_call(self) -> None:
        agent = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value={"structured_response": _BoundaryResponse(value="ok")}
            )
        )
        with (
            patch("agents.runtime.build_llm_client", return_value=object()),
            patch("agents.runtime.build_standard_stack", return_value=[]),
            patch("agents.runtime.create_agent", return_value=agent),
            patch(
                "agents.runtime.begin_agent_invoke",
                side_effect=OSError("diagnostic disk unavailable"),
            ),
            patch("agents.runtime.end_agent_invoke") as end_recording,
        ):
            with self.assertRaisesRegex(OSError, "diagnostic disk unavailable"):
                await run_structured_agent(
                    agent_name="boundary",
                    invoke_id="run",
                    system_prompt="submit when done",
                    tools=[],
                    response_format=_BoundaryResponse,
                    user_message="inspect",
                    run_dir=Path("unused"),
                    cost_budget=CostBudget(limit_usd=1.0),
                )

        agent.ainvoke.assert_not_awaited()
        end_recording.assert_not_called()

    async def test_missing_submit_tool_fails_after_one_invoke(self) -> None:
        skipped = {"messages": [SimpleNamespace(content="후보만 메모하고 끝냄")]}
        agent = SimpleNamespace(ainvoke=AsyncMock(return_value=skipped))
        with (
            patch("agents.runtime.build_llm_client", return_value=object()),
            patch("agents.runtime.build_standard_stack", return_value=[]),
            patch("agents.runtime.create_agent", return_value=agent),
            patch("agents.runtime.begin_agent_invoke"),
            patch("agents.runtime.end_agent_invoke"),
        ):
            with self.assertRaises(StructuredResponseMissingError):
                await run_structured_agent(
                    agent_name="boundary",
                    invoke_id="run",
                    system_prompt="submit when done",
                    tools=[],
                    response_format=_BoundaryResponse,
                    user_message="inspect",
                    run_dir=Path("unused"),
                    cost_budget=CostBudget(limit_usd=1.0),
                )

        self.assertEqual(agent.ainvoke.await_count, 1)

    async def test_persistent_missing_submit_still_fails_loudly(self) -> None:
        agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": []}))
        with (
            patch("agents.runtime.build_llm_client", return_value=object()),
            patch("agents.runtime.build_standard_stack", return_value=[]),
            patch("agents.runtime.create_agent", return_value=agent),
            patch("agents.runtime.begin_agent_invoke"),
            patch("agents.runtime.end_agent_invoke"),
        ):
            with self.assertRaises(StructuredResponseMissingError):
                await run_structured_agent(
                    agent_name="boundary",
                    invoke_id="run",
                    system_prompt="submit when done",
                    tools=[],
                    response_format=_BoundaryResponse,
                    user_message="inspect",
                    run_dir=Path("unused"),
                    cost_budget=CostBudget(limit_usd=1.0),
                )
        self.assertEqual(agent.ainvoke.await_count, 1)

    async def test_prompt_context_is_cleared_when_final_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = tracing._AgentInvokeState(
                agent_name="test",
                invoke_id="run",
                out_path=Path(temporary_directory) / "agent.json",
                system_prompt="system",
            )
            tracing._invoke_var.set(state)
            with patch.object(tracing, "_write_json", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    tracing.end_agent_invoke()

        self.assertIsNone(tracing._invoke_var.get())

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
                    return_value=SimpleNamespace(
                        provider="openai", structured_output_method="function_calling"
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
        self.assertEqual("function_calling", error_record.call_args.args[7])

    async def test_response_cache_hit_is_recorded_as_a_metered_call(self) -> None:
        """적중도 LLM 호출이다 — 적재를 건너뛰면 사용량 통계가 호출 수를 과소 보고한다.

        서버를 안 불렀으므로 토큰 4종·비용은 0 이고, 서버측 프리픽스 캐시를 뜻하는
        ``cached_tokens`` 가 아니라 별도 필드 ``response_cache_hit`` 로 구분한다.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts_dir = Path(temporary_directory)
            (prompts_dir / "boundary.yaml").write_text(
                "system: test system\nuser_template: '{value}'\nmodel: test-model\n",
                encoding="utf-8",
            )
            usage_path = prompts_dir / "usage.jsonl"
            budget = CostBudget(limit_usd=1.0)

            with (
                patch(
                    "llm.runners.yaml_llm_runner.load_llm_config",
                    return_value=SimpleNamespace(
                        provider="openai",
                        api_base="https://api.example/v1",
                        model="cfg-model",
                        max_tokens=321,
                        sampling=SimpleNamespace(temperature=0.2, top_p=0.8),
                        reasoning_effort="none",
                        extra_body={"seed": 7},
                        structured_output_method="function_calling",
                    ),
                ),
                patch(
                    "llm.runners.yaml_llm_runner.response_cache.is_enabled",
                    return_value=True,
                ),
                patch(
                    "llm.runners.yaml_llm_runner.response_cache.make_key_from_schema",
                    return_value="k" * 64,
                ) as make_cache_key,
                patch(
                    "llm.runners.yaml_llm_runner.response_cache.load",
                    return_value=_BoundaryResponse(value="cached"),
                ),
                patch(
                    "llm.runners.yaml_llm_runner._resolve_usage_log_path",
                    return_value=usage_path,
                ),
                patch("llm.runners.yaml_llm_runner.write_rule_record") as prompt_record,
                patch("llm.runners.yaml_llm_runner.build_llm_client") as build_client,
            ):
                result = await run_yaml_rule(
                    prompts_dir=prompts_dir,
                    rule_name="boundary",
                    user_values={"value": "input"},
                    response_model=_BoundaryResponse,
                    budget=budget,
                )

            build_client.assert_not_called()
            self.assertEqual(321, make_cache_key.call_args.kwargs["max_tokens"])
            self.assertEqual(0.2, make_cache_key.call_args.kwargs["temperature"])
            self.assertEqual(0.8, make_cache_key.call_args.kwargs["top_p"])
            self.assertEqual({"seed": 7}, make_cache_key.call_args.kwargs["extra_body"])
            self.assertEqual("cached", result.value)
            prompt_record.assert_called_once()
            evidence = prompt_record.call_args.kwargs
            self.assertIsNone(evidence["response"]["raw"])
            self.assertEqual(
                {"value": "cached"}, evidence["response"]["parsed"],
            )
            self.assertTrue(evidence["metadata"]["response_cache_hit"])
            self.assertEqual(
                "function_calling",
                evidence["metadata"]["structured_output_method"],
            )

            lines = usage_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(1, len(lines), "적중 1건에 usage 기록이 1줄이 아니다")
            record = json.loads(lines[0])

        self.assertTrue(record["response_cache_hit"])
        self.assertEqual("rule:boundary", record["agent"])
        self.assertEqual("test-model", record["model"])
        self.assertEqual(
            [0, 0, 0, 0, 0.0],
            [record["input_tokens"], record["output_tokens"], record["cached_tokens"],
             record["total_tokens"], record["cost_usd"]],
        )
        self.assertEqual(0.0, budget.spent_usd, "적중에 비용을 물리면 회계가 부풀려진다")

    async def test_server_call_is_recorded_as_not_a_cache_hit(self) -> None:
        """실제 호출 줄은 ``response_cache_hit=False`` — 두 종류가 한 필드에서 안 섞인다."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            usage_path = Path(temporary_directory) / "usage.jsonl"
            append_usage_record(
                usage_path,
                "rule:boundary",
                TokenUsageRecord(model="m", input_tokens=10, output_tokens=5,
                                 cached_tokens=4, total_tokens=15),
                0.5,
            )
            record = json.loads(usage_path.read_text(encoding="utf-8").strip())

        self.assertFalse(record["response_cache_hit"])
        self.assertEqual(4, record["cached_tokens"],
                         "서버측 프리픽스 캐시 토큰은 그대로 보존돼야 한다")


if __name__ == "__main__":
    unittest.main()
