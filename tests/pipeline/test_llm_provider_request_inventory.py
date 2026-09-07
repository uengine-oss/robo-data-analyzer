"""Serialize every production model boundary without contacting a provider."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.utils.function_calling import convert_to_openai_tool

from agents.agent_contracts import ResidualPatternResult, ScanProposal
from agents.external_agent import _user_message as external_user_message
from agents.agent_runtime import _wrap_pydantic_as_submit_tool
from agents.scan_agent import _build_scan_tools, _build_user_message as scan_user_message
from agents.tools.pattern_mining import make_structure_candidate_tool
from agents.tools.sample_tools import make_sample_exploration_tools
from agents.tools.verify_tools import make_verify_external_rule_tool
from graph.schema.graph_nodes import CodeNode
from llm.runners.prompt_rules import _load_rule, render_rule_user_content
from tests.pipeline.semantic_prompt_paths import prompt_dir
from llm.structured_output import (
    STRUCTURED_RESPONSE_NAME,
    provider_structured_output_binding_schema,
)
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.meaning.table.response_schema import (
    batch_function_table_usages_response_model,
    function_table_usages_response_model,
)
from pipeline.meaning.rule.meaning_frame import (
    batch_rule_meaning_response_model,
    rule_meaning_response_model,
)
from pipeline.models.semantic_frame import SlotSpec
from pipeline.meaning.summary.response_schema import frame_summary_response_model
from pipeline.meaning.table.description_frame import (
    build_table_description_plans,
    table_description_frame_response_model,
)
from pipeline.stages.scan.external_skills import ExternalFormatProposal
from pipeline.meaning.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)
from pipeline.execution.run_output_context import (
    reset_response_locale,
    set_response_locale,
)


ROOT = Path(__file__).resolve().parents[2]
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_FORBIDDEN_RESPONSE_KEYS = {
    "id", "stable_id", "source_id", "target_id", "owner_id", "evidence_id",
    "hash", "sha256", "path", "file_path", "start_line", "end_line",
    "offset", "range", "grammar", "candidate", "resolution", "confidence",
    "question", "evidence",
}


@dataclass(frozen=True)
class ProviderRequest:
    name: str
    transport: str
    system: str
    user: str
    response_schema: dict
    tools: tuple[dict, ...] = ()
    sealed_skt_calls: int = 0

    def wire(self) -> str:
        return json.dumps({
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": self.user},
            ],
            "response_schema": self.response_schema,
            "tools": self.tools,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _yaml_request(
    name: str,
    prompts_dir: Path,
    rule_name: str,
    values: dict[str, str],
    response_model,
    *,
    sealed_skt_calls: int = 0,
) -> ProviderRequest:
    rule = _load_rule(prompts_dir, rule_name)
    method = rule.get("structured_output_method") or "json_schema"
    schema = provider_structured_output_binding_schema(
        response_model.model_json_schema(), method=method,
    )
    locale_token = set_response_locale("ko")
    try:
        user = render_rule_user_content(rule, values)
    finally:
        reset_response_locale(locale_token)
    return ProviderRequest(
        name=name,
        transport=f"yaml:{method}",
        system=str(rule.get("system") or ""),
        user=user,
        response_schema=schema,
        sealed_skt_calls=sealed_skt_calls,
    )


def _provider_tools(tools: list) -> tuple[dict, ...]:
    return tuple(convert_to_openai_tool(tool, strict=True) for tool in tools)


def _table_usage_plan():
    node = CodeNode(
        id="code:orders", name="select_orders", signature="select_orders",
        file_path="orders.sql", start_line=1, end_line=1, label="SELECT",
    )
    context = AnalysisContext(
        id=node.id, signature=node.signature, file_path=node.file_path,
        start_line=1, end_line=1,
        reads=["db:orders"], direct_reads=["db:orders"],
        data_object_usages=(DataObjectUsageEvidence(
            source_id=node.id, raw_reference="orders", name="orders",
            access="READ", evidence_file=node.file_path, evidence_line=1,
            table_id="db:orders", resolution="exact",
        ),),
        extra={"ast_statements": ()},
    )
    profile, specs = build_code_semantic_plan(node, context)
    return profile, semantic_concern_slots(specs)["table_usage_meanings"]


def _summary_model(strategy: str):
    slots = (SlotSpec(
        slot_id="SL:0123456789ab:FUNCTION:WHOLE_FUNCTION:root",
        family="FUNCTION", role="WHOLE_FUNCTION",
        target_ref="EV:internal", evidence_refs=("EV:internal",),
    ),)
    return frame_summary_response_model(strategy, slots)


def _table_model():
    evidence = {
        "table": {"db": "db", "schema": "public", "name": "orders"},
        "columns": [{"name": "order_id"}],
    }
    _table_plan, column_plans = build_table_description_plans(
        "db:public.orders", evidence,
    )
    return table_description_frame_response_model(column_plans)


def _schema_property_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(str(key) for key in properties)
        for item in value.values():
            names.update(_schema_property_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_schema_property_names(item))
    return names


def _schema_annotations(
    value: object,
    *,
    property_map: bool = False,
) -> set[str]:
    annotations: set[str] = set()
    if isinstance(value, dict):
        if not property_map:
            annotations.update(
                key for key in ("title", "description") if key in value
            )
        for key, item in value.items():
            annotations.update(_schema_annotations(
                item,
                property_map=not property_map and key == "properties",
            ))
    elif isinstance(value, list):
        for item in value:
            annotations.update(_schema_annotations(item))
    return annotations


class ProductionProviderRequestInventoryTest(unittest.TestCase):
    def _inventory(self) -> tuple[ProviderRequest, ...]:
        profile, usage_slots = _table_usage_plan()
        usage_model = function_table_usages_response_model(
            profile, usage_slots, ("db:orders",), (),
        )
        usage_assignments = {"N1": {
            "profile": profile, "slot_specs": usage_slots,
            "reads": ("db:orders",), "writes": (),
        }}
        batch_usage_model = batch_function_table_usages_response_model(
            usage_assignments,
        )
        rule_evidence = {
            "conditions": {"C1": "ready"},
            "effects": {"E1": {
                "op": "ret", "target": None, "value": "OK",
                "from_source": True,
            }},
            "rows": {"R1": {"when": ["C1"], "direct": ["E1"]}},
        }
        summary_values = {
            "summary_context": "## [STRUCTURE]\n1 : FUNCTION",
            "code_body": "1 | return 0;",
        }
        framework_summary = prompt_dir("framework", "code_summary")
        dbms_summary = prompt_dir("dbms", "code_summary_node")
        framework_table = prompt_dir("framework", "code_table_usage_meanings")
        dbms_table = prompt_dir("dbms", "code_table_usage_meanings_batch")
        framework_rule = prompt_dir("framework", "code_rule_meanings")
        dbms_rule = prompt_dir("dbms", "code_rule_meanings_batch")

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project_root = Path(temporary.name)
        scan_tools = _build_scan_tools(project_root, [], [])
        scan_tools.append(_wrap_pydantic_as_submit_tool(
            ScanProposal, tool_name="submit_scan",
        ))
        external_tools = [
            make_structure_candidate_tool(["REC A payload\n"]),
            *make_sample_exploration_tools(["REC A payload\n"]),
            make_verify_external_rule_tool(
                ["REC A payload\n"], ["unrelated\n"],
            ),
            _wrap_pydantic_as_submit_tool(
                ExternalFormatProposal, tool_name="submit_external",
            ),
        ]
        scan_schema = convert_to_openai_tool(
            scan_tools[-1], strict=True,
        )["function"]["parameters"]
        external_schema = convert_to_openai_tool(
            external_tools[-1], strict=True,
        )["function"]["parameters"]

        return (
            ProviderRequest(
                "scan", "agent-tools",
                (ROOT / "agents" / "prompts" / "scan.md").read_text(encoding="utf-8"),
                scan_user_message({
                    "extension_counts": {"c": 1},
                    "manifest_paths": [],
                    "scanner_category_file_counts": {"source": 1},
                }, []),
                scan_schema,
                _provider_tools(scan_tools),
            ),
            ProviderRequest(
                "external", "agent-tools",
                (ROOT / "agents" / "prompts" / "external.md").read_text(
                    encoding="utf-8",
                ),
                external_user_message(".rec"),
                external_schema,
                _provider_tools(external_tools),
            ),
            _yaml_request(
                "residual", ROOT / "agents" / "prompts", "residual", {
                    "confirmed_exclusions": "[]",
                    "framework_runtime_functions": "(없음)",
                    "ddl_tables": "orders",
                    "confirmed_patterns": "{}",
                    "current_ddl_transform": '{"ddl_naming_rule":"as_is"}',
                    "residual_cases": "[]",
                }, ResidualPatternResult,
            ),
            _yaml_request(
                "framework_summary", framework_summary, "code_summary",
                summary_values, _summary_model("framework"),
                sealed_skt_calls=17,
            ),
            _yaml_request(
                "dbms_summary", dbms_summary, "code_summary_node",
                summary_values, _summary_model("dbms"),
            ),
            _yaml_request(
                "framework_table_usage", framework_table,
                "code_table_usage_meanings", {
                    "assigned_table_usages": (
                        '{"FT1":{"access_mode":"READ","occurrences":'
                        '[{"line":1}],"table":"orders"}}'
                    ),
                    "code_body": "1 | SELECT * FROM orders",
                }, usage_model, sealed_skt_calls=4,
            ),
            _yaml_request(
                "dbms_table_usage", dbms_table,
                "code_table_usage_meanings_batch", {
                    "table_usage_packets": (
                        '{"N1":{"assigned_table_usages":{"FT1":'
                        '{"access_mode":"READ","occurrences":[{"line":1}],'
                        '"table":"orders"}},"numbered_source":'
                        '"1 | SELECT * FROM orders"}}'
                    ),
                }, batch_usage_model,
            ),
            _yaml_request(
                "framework_rule", framework_rule, "code_rule_meanings", {
                    "rule_packet": json.dumps({
                        "source": "1 | return OK;",
                        "conditions": {"C1": "ready"},
                        "effects": {"E1": {"kind": "ret", "value": "OK"}},
                        "rows": {"R1": {
                            "kind": "return", "when": ["C1"],
                            "effects": ["E1"],
                        }},
                    }, ensure_ascii=False, separators=(",", ":")),
                }, rule_meaning_response_model(("C1",), ("E1",), ("R1",)),
                sealed_skt_calls=17,
            ),
            _yaml_request(
                "dbms_rule", dbms_rule, "code_rule_meanings_batch", {
                    "rule_packets": json.dumps(
                        {"N1": rule_evidence},
                        ensure_ascii=False, separators=(",", ":"),
                    ),
                }, batch_rule_meaning_response_model({"N1": rule_evidence}),
            ),
            _yaml_request(
                "table_description", framework_table, "table_description", {
                    "table_and_column_meaning_inputs": (
                        '{"columns":{"order_id":{}},"table":'
                        '{"observed_operations":[{"access":"READ",'
                        '"operation":"SELECT"}]}}'
                    ),
                }, _table_model(), sealed_skt_calls=2,
            ),
        )

    def test_exactly_ten_boundaries_serialize_through_production_builders(self):
        inventory = self._inventory()
        self.assertEqual(10, len(inventory))
        self.assertEqual({
            "scan", "external", "residual", "framework_summary",
            "dbms_summary", "framework_table_usage", "dbms_table_usage",
            "framework_rule", "dbms_rule", "table_description",
        }, {item.name for item in inventory})
        for item in inventory:
            with self.subTest(boundary=item.name):
                self.assertTrue(item.system.strip())
                self.assertTrue(item.user.strip())
                self.assertFalse(_PLACEHOLDER.search(item.system))
                self.assertFalse(_PLACEHOLDER.search(item.user))
                json.loads(item.wire())

    def test_response_wires_have_no_internal_identity_or_schema_prose(self):
        for item in self._inventory():
            with self.subTest(boundary=item.name):
                semantic_schema = dict(item.response_schema)
                if item.transport.startswith(("yaml:", "search:")):
                    self.assertEqual(
                        STRUCTURED_RESPONSE_NAME,
                        semantic_schema.pop("title"),
                    )
                self.assertEqual(set(), _schema_annotations(semantic_schema))
                self.assertFalse(
                    _schema_property_names(semantic_schema)
                    & _FORBIDDEN_RESPONSE_KEYS,
                )

    def test_sealed_skt_plan_is_only_the_four_framework_families_and_40_calls(self):
        calls = {
            item.name: item.sealed_skt_calls
            for item in self._inventory()
            if item.sealed_skt_calls
        }
        self.assertEqual({
            "framework_summary": 17,
            "framework_table_usage": 4,
            "framework_rule": 17,
            "table_description": 2,
        }, calls)
        self.assertEqual(40, sum(calls.values()))

    def test_agent_prompts_remain_compact_without_dropping_verification_gates(self):
        inventory = {item.name: item for item in self._inventory()}
        scan = inventory["scan"]
        external = inventory["external"]
        self.assertLessEqual(len(scan.system), 2_200)
        self.assertLessEqual(len(external.system), 1_500)
        for marker in (
            "선택-source", "next_offset|next_start_line", "read_skill",
            "verify_table_regex", "submit_scan", "정확히 한 번",
        ):
            self.assertIn(marker, scan.system)
        for marker in (
            "held-out", "negative", "next_offset", "sample_overview",
            "verify_external_rule", "submit_external", "정확히 한 번",
        ):
            self.assertIn(marker, external.system)


if __name__ == "__main__":
    unittest.main()
