"""Machine inventory for every production LLM response boundary."""
from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from agents.agent_contracts import ResidualPatternResult, ScanProposal
from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.code_analysis_output import RuleItem
from pipeline.models.data_object_usage import DataObjectUsageEvidence
from pipeline.meaning.rule.meaning_frame import (
    batch_rule_meaning_response_model,
    rule_meaning_response_model,
)
from pipeline.meaning.summary.response_schema import (
    frame_summary_response_model,
)
from pipeline.models.semantic_frame import (
    SlotSpec,
)
from pipeline.meaning.table.description_frame import (
    build_table_description_plans,
    table_description_frame_response_model,
)
from pipeline.meaning.table.response_schema import (
    batch_function_table_usages_response_model,
    function_table_usages_response_model,
)
from pipeline.stages.scan.external_skills import ExternalFormatProposal
from pipeline.meaning.semantic_frame_planner import (
    build_code_semantic_plan,
    semantic_concern_slots,
)


def _assert_closed_required(case: unittest.TestCase, model, required: set[str]) -> None:
    schema = model.model_json_schema()
    variants = schema.get("oneOf") or (schema,)
    for variant in variants:
        if "$ref" in variant:
            variant = schema["$defs"][variant["$ref"].rsplit("/", 1)[-1]]
        case.assertEqual(required, set(variant.get("required", ())))
        case.assertEqual(required, set(variant.get("properties", {})))
        case.assertFalse(variant.get("additionalProperties", True))


def _assert_no_schema_titles(
    case: unittest.TestCase,
    value,
    *,
    property_map: bool = False,
) -> None:
    if isinstance(value, dict):
        if not property_map:
            case.assertNotIn("title", value)
            case.assertNotIn("description", value)
        for key, item in value.items():
            _assert_no_schema_titles(
                case,
                item,
                property_map=not property_map and key == "properties",
            )
    elif isinstance(value, list):
        for item in value:
            _assert_no_schema_titles(case, item)


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
            source_id=node.id,
            raw_reference="orders",
            name="orders",
            access="READ",
            evidence_file=node.file_path,
            evidence_line=1,
            table_id="db:orders",
            resolution="exact",
        ),),
        extra={
            "ast_statements": (),
        },
    )
    profile, specs = build_code_semantic_plan(node, context)
    return profile, semantic_concern_slots(specs)["table_usage_meanings"]


class LlmResponseBoundaryInventoryTest(unittest.TestCase):
    def test_non_semantic_simple_outputs_require_every_prompt_owned_key(self) -> None:
        boundaries = (
            (ResidualPatternResult, {
                "table_access_patterns", "ddl_transform_override",
            }),
            (ExternalFormatProposal, {"rules"}),
        )
        for model, required in boundaries:
            with self.subTest(model=model.__name__):
                _assert_closed_required(self, model, required)
                _assert_no_schema_titles(self, model.model_json_schema())

    def test_dynamic_semantic_envelopes_are_closed_and_required(self) -> None:
        table_evidence = {
            "table": {"db": "db", "schema": "public", "name": "orders"},
            "columns": [{"name": "order_id"}],
        }
        table_plan, column_plans = build_table_description_plans(
            "db:public.orders", table_evidence,
        )
        summary_slots = (SlotSpec(
            slot_id="SL:0123456789ab:FUNCTION:WHOLE_FUNCTION:root",
            family="FUNCTION",
            role="WHOLE_FUNCTION",
            target_ref="EV:target",
            evidence_refs=("EV:target",),
        ),)
        usage_profile, usage_slots = _table_usage_plan()
        boundaries = (
            (frame_summary_response_model("framework", summary_slots), {
                "summary", "stereotype",
            }),
            (frame_summary_response_model("dbms", summary_slots), {
                "summary", "stereotype",
            }),
            (function_table_usages_response_model(
                usage_profile, usage_slots, ("db:orders",), (),
            ), {"FT1"}),
            (batch_function_table_usages_response_model({
                "N1": {
                    "profile": usage_profile,
                    "slot_specs": usage_slots,
                    "reads": ("db:orders",),
                    "writes": (),
                },
            }), {"nodes"}),
            (table_description_frame_response_model(
                column_plans,
            ), {"logical_name", "description", "columns"}),
            (rule_meaning_response_model(("C1",), ("E1",), ("R1",)), {
                "conditions", "effects", "rows",
            }),
            (batch_rule_meaning_response_model({
                "N1": {
                    "conditions": {"C1": "ready"},
                    "effects": {"E1": {
                        "op": "ret", "target": None, "value": "OK",
                        "from_source": True,
                    }},
                    "rows": {"R1": {"when": ["C1"], "direct": ["E1"]}},
                },
            }), {"nodes"}),
        )
        for model, required in boundaries:
            with self.subTest(model=model.__name__):
                _assert_closed_required(self, model, required)
                schema = model.model_json_schema()
                _assert_no_schema_titles(self, schema)
                self.assertNotRegex(str(schema), r"_[0-9a-f]{12}")

    def test_summary_schema_is_target_invariant_and_has_no_binding_keys(self) -> None:
        first = (SlotSpec(
            slot_id="SL:0123456789ab:FUNCTION:WHOLE_FUNCTION:first",
            family="FUNCTION", role="WHOLE_FUNCTION",
            target_ref="EV:first", evidence_refs=("EV:first",),
        ),)
        second = (SlotSpec(
            slot_id="SL:fedcba987654:FUNCTION:WHOLE_FUNCTION:second",
            family="FUNCTION", role="WHOLE_FUNCTION",
            target_ref="EV:second", evidence_refs=("EV:second",),
        ),)
        first_schema = frame_summary_response_model("framework", first).model_json_schema()
        second_schema = frame_summary_response_model("framework", second).model_json_schema()
        self.assertEqual(first_schema, second_schema)
        self.assertEqual({"summary", "stereotype"}, set(first_schema["properties"]))

    def test_final_rule_is_closed_and_not_a_direct_llm_response(self) -> None:
        valid = {
            "anchor_line": 7,
            "structural_condition": 'F("ready")',
            "structural_effects": [],
        }
        RuleItem.model_validate(valid)
        with self.assertRaises(ValidationError):
            RuleItem.model_validate({**valid, "retired_narrative": "근거 행의 업무 의미"})
        with self.assertRaises(ValidationError):
            RuleItem.model_validate({**valid, "business_meaning": "invented"})

    def test_matched_scan_omissions_are_explicit_deterministic_hydration(self) -> None:
        schema = ScanProposal.model_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(["matched_skill"], schema["required"])
        self.assertNotIn("table_access_rules", schema["required"])
        self.assertNotIn("evidence", schema["properties"])
        self.assertNotIn('"id"', json.dumps(schema))
        self.assertNotIn("evidence_locations", schema["properties"])
        self.assertNotIn("confidence", schema["properties"])
        self.assertNotIn("analysis_status", schema["properties"])
        self.assertNotIn("no_match_reason", schema["properties"])

    def test_pattern_proposal_boundaries_do_not_ask_the_model_for_ids(self) -> None:
        for model in (ScanProposal, ResidualPatternResult, ExternalFormatProposal):
            with self.subTest(model=model.__name__):
                schema = model.model_json_schema()
                rendered = json.dumps(schema)
                self.assertNotIn('"id"', rendered)
                if model is ExternalFormatProposal:
                    self.assertNotIn('"name"', rendered)
                    self.assertNotIn('"key_group"', rendered)


if __name__ == "__main__":
    unittest.main()
