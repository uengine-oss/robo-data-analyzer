from __future__ import annotations

import unittest

from pydantic import ValidationError

from pipeline.languages import require_module_stereotype, require_stereotype
from pipeline.models.module_summary_response_schema import module_summary_response_model


class ClosedSemanticResponseSchemaTest(unittest.TestCase):
    def test_code_stereotype_validation_never_erases_invalid_meaning(self) -> None:
        self.assertEqual("Query", require_stereotype("Query", "framework"))
        for invalid in ("", "DataQuery", "Unknown"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                require_stereotype(invalid, "framework")

    def test_module_schema_requires_every_field_and_strategy_enum(self) -> None:
        model = module_summary_response_model("framework")
        schema = model.model_json_schema()
        self.assertEqual(
            {"summary", "stereotype", "logical_name"}, set(schema["required"])
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("Service", schema["properties"]["stereotype"]["enum"])
        self.assertNotIn(
            "BatchProcessing", schema["properties"]["stereotype"]["enum"]
        )
        valid = {
            "summary": "주문 처리를 조정한다.",
            "stereotype": "Service",
            "logical_name": "주문 처리",
        }
        self.assertEqual("Service", model.model_validate(valid).stereotype)
        for missing in valid:
            with self.subTest(missing=missing), self.assertRaises(ValidationError):
                model.model_validate({
                    key: value for key, value in valid.items() if key != missing
                })
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "stereotype": "BatchProcessing"})
        with self.assertRaises(ValidationError):
            model.model_validate({**valid, "unexpected": "value"})

    def test_module_stereotype_validation_is_fail_closed(self) -> None:
        self.assertEqual(
            "BatchProcessing",
            require_module_stereotype("BatchProcessing", "dbms"),
        )
        for invalid in ("", "Service", "Unknown"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                require_module_stereotype(invalid, "dbms")


if __name__ == "__main__":
    unittest.main()
