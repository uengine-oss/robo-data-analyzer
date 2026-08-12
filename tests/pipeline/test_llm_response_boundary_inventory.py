"""Machine inventory for every production LLM response boundary.

The test distinguishes fields that the model must emit (required even when
their value may be empty) from ScanResult fields that the scan prompt
explicitly tells a matched-skill agent to omit for deterministic hydration.
"""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from agents.contracts import ExternalCallReview, ResidualPatternResult, ScanResult
from pipeline.models.code_analysis_output import (
    ComponentNamingOutput,
    ModulePreAnalysisOutput,
)
from pipeline.models.summary_response_schema import (
    batch_summary_response_model,
    summary_response_model,
)
from pipeline.models.module_summary_response_schema import module_summary_response_model
from pipeline.models.rule_response_schema import (
    batch_rules_response_model,
    rules_response_model,
)
from pipeline.models.table_insight_response_schema import (
    batch_table_insights_response_model,
    table_insights_response_model,
)
from pipeline.steps.step4_scan_project.external_skills import ExternalSkill
from pipeline.steps.step8_post_processing.enrich_table_description_step import (
    _table_description_response_model,
)


def _assert_closed_required(
    case: unittest.TestCase,
    model,
    required: set[str],
) -> None:
    schema = model.model_json_schema()
    variants = schema.get("oneOf") or (schema,)
    for variant in variants:
        if "$ref" in variant:
            variant = schema["$defs"][variant["$ref"].rsplit("/", 1)[-1]]
        case.assertEqual(required, set(variant.get("required", ())))
        case.assertEqual(required, set(variant.get("properties", {})))
        case.assertFalse(variant.get("additionalProperties", True))


class LlmResponseBoundaryInventoryTest(unittest.TestCase):
    def test_direct_simple_outputs_require_every_prompt_owned_key(self) -> None:
        boundaries = (
            (ModulePreAnalysisOutput, {"intent"}),
            (ComponentNamingOutput, {"name"}),
            (ExternalCallReview, {"drops"}),
            (
                ResidualPatternResult,
                {
                    "table_access_patterns",
                    "ddl_transform_override",
                    "no_pattern_reason",
                },
            ),
            (
                ExternalSkill,
                {
                    "name",
                    "extensions",
                    "content_signatures",
                    "rules",
                    "explanation",
                    "provenance",
                },
            ),
            (
                module_summary_response_model("framework"),
                {"summary", "stereotype", "logical_name"},
            ),
        )
        for model, required in boundaries:
            with self.subTest(model=model.__name__):
                _assert_closed_required(self, model, required)

    def test_dynamic_analysis_envelopes_are_closed_and_required(self) -> None:
        boundaries = (
            (
                summary_response_model("framework"),
                {"result"},
            ),
            (
                batch_summary_response_model(("1-2",), "dbms"),
                {"nodes"},
            ),
            (
                rules_response_model([1]),
                {"rules"},
            ),
            (
                batch_rules_response_model({"1-2": [1]}),
                {"nodes"},
            ),
            (
                table_insights_response_model(("db:orders",), ()),
                {"table_insights"},
            ),
            (
                batch_table_insights_response_model({
                    "1-2": {
                        "reads": ("db:orders",),
                        "writes": (),
                        "table_ops": {"db:orders": "SELECT"},
                    }
                }),
                {"nodes"},
            ),
            (
                _table_description_response_model({
                    "table": {"db": "db", "schema": "public", "name": "orders"},
                    "columns": [],
                }),
                {
                    "db", "schema", "name", "meaning", "columns",
                },
            ),
        )
        for model, required in boundaries:
            with self.subTest(model=model.__name__):
                _assert_closed_required(self, model, required)

    def test_rule_narrative_is_the_only_model_owned_leaf(self) -> None:
        model = rules_response_model([7])
        valid = {"rules": {"R7": {"nl": "근거 행의 업무 의미"}}}
        model.model_validate(valid)
        with self.assertRaises(ValidationError):
            model.model_validate({"rules": {"R7": {}}})
        for structural_field, value in (
            ("cond", ["C7"]), ("then", ["E1"]), ("extra", []),
        ):
            with self.subTest(field=structural_field), self.assertRaises(ValidationError):
                model.model_validate({
                    "rules": {"R7": {"nl": "근거 행의 업무 의미", structural_field: value}},
                })
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "unexpected": "value"})

    def test_empty_table_columns_key_is_required_not_defaulted(self) -> None:
        model = _table_description_response_model({
            "table": {"db": "db", "schema": "public", "name": "orders"},
            "columns": [],
        })
        valid = {
            "db": "db",
            "schema": "public",
            "name": "orders",
            "meaning": {
                "logical_name": "",
                "description": "",
                "evidence_status": "insufficient",
                "missing_context": ["테이블 업무 의미 근거"],
            },
            "columns": [],
        }
        model.model_validate(valid)
        with self.assertRaises(ValidationError):
            model.model_validate({key: value for key, value in valid.items() if key != "columns"})
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "unexpected": "value"})

    def test_matched_scan_omissions_are_explicit_deterministic_hydration(self) -> None:
        schema = ScanResult.model_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertTrue({"confidence", "evidence"}.issubset(schema["required"]))
        self.assertNotIn("table_access_rules", schema["required"])
        self.assertNotIn("codebase_notes", schema["required"])


if __name__ == "__main__":
    unittest.main()
