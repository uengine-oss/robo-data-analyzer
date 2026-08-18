"""Machine inventory for every production LLM response boundary."""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from agents.contracts import ExternalCallReview, ResidualPatternResult, ScanResult
from pipeline.models.aggregate_semantic_frame import (
    aggregate_response_model,
    build_aggregate_semantic_plan,
)
from pipeline.models.code_analysis_output import RuleItem
from pipeline.models.summary_response_schema import (
    batch_summary_response_model,
    summary_response_model,
)
from pipeline.models.table_description_frame import (
    build_table_description_plans,
    table_description_frame_response_model,
)
from pipeline.models.table_insight_response_schema import (
    batch_table_insights_response_model,
    table_insights_response_model,
)
from pipeline.steps.step4_scan_project.external_skills import ExternalSkill


def _assert_closed_required(case: unittest.TestCase, model, required: set[str]) -> None:
    schema = model.model_json_schema()
    variants = schema.get("oneOf") or (schema,)
    for variant in variants:
        if "$ref" in variant:
            variant = schema["$defs"][variant["$ref"].rsplit("/", 1)[-1]]
        case.assertEqual(required, set(variant.get("required", ())))
        case.assertEqual(required, set(variant.get("properties", {})))
        case.assertFalse(variant.get("additionalProperties", True))


class LlmResponseBoundaryInventoryTest(unittest.TestCase):
    def test_non_semantic_simple_outputs_require_every_prompt_owned_key(self) -> None:
        boundaries = (
            (ExternalCallReview, {"drops"}),
            (ResidualPatternResult, {
                "table_access_patterns", "ddl_transform_override", "no_pattern_reason",
            }),
            (ExternalSkill, {
                "name", "extensions", "content_signatures", "rules",
                "explanation", "provenance",
            }),
        )
        for model, required in boundaries:
            with self.subTest(model=model.__name__):
                _assert_closed_required(self, model, required)

    def test_dynamic_semantic_envelopes_are_closed_and_required(self) -> None:
        _profile, module_specs = build_aggregate_semantic_plan(
            target_id="module:orders", target_type="MODULE", owner_id="",
            file="orders.py", start_line=1, end_line=10, family="MODULE",
            member_frames=(), include_responsibility=True,
        )
        table_evidence = {
            "table": {"db": "db", "schema": "public", "name": "orders"},
            "columns": [{"name": "order_id"}],
        }
        table_plan, column_plans = build_table_description_plans(
            "db:public.orders", table_evidence,
        )
        boundaries = (
            (summary_response_model("framework"), {"result"}),
            (batch_summary_response_model(("1-2",), "dbms"), {"nodes"}),
            (table_insights_response_model(("db:orders",), ()), {"table_insights"}),
            (batch_table_insights_response_model({
                "1-2": {"reads": ("db:orders",), "writes": (),
                        "table_ops": {"db:orders": "SELECT"}},
            }), {"nodes"}),
            (aggregate_response_model(
                "ModuleFrameBoundary", module_specs,
                stereotype_values=("DomainModule", "UtilityModule"),
            ), {"slots", "stereotype"}),
            (table_description_frame_response_model(
                table_evidence, table_plan, column_plans,
            ), {"db", "schema", "name", "slots", "columns"}),
        )
        for model, required in boundaries:
            with self.subTest(model=model.__name__):
                _assert_closed_required(self, model, required)

    def test_rule_is_a_closed_deterministic_projection_not_an_llm_boundary(self) -> None:
        valid = {"anchor_line": 7, "cond": 'F("ready")', "then": [], "nl": ""}
        RuleItem.model_validate(valid)
        with self.assertRaises(ValidationError):
            RuleItem.model_validate({**valid, "nl": "근거 행의 업무 의미"})
        with self.assertRaises(ValidationError):
            RuleItem.model_validate({**valid, "business_meaning": "invented"})

    def test_matched_scan_omissions_are_explicit_deterministic_hydration(self) -> None:
        schema = ScanResult.model_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue({"confidence", "evidence"}.issubset(schema["required"]))
        self.assertNotIn("table_access_rules", schema["required"])
        self.assertNotIn("codebase_notes", schema["required"])


if __name__ == "__main__":
    unittest.main()
