from __future__ import annotations

import unittest

from pydantic import ValidationError

from pipeline.models.summary_response_schema import (
    batch_summary_response_model,
    normalize_batch_summary_response,
    normalize_summary_response,
    summary_response_model,
)


def _summary(**overrides):
    value = {
        "summary": "요청 상태에 따라 주문을 조회하고 검증을 위임해 결과나 오류를 반환한다.",
        "stereotype": "Query",
        "questions": [],
    }
    value.update(overrides)
    return value


class SummaryResponseSchemaTest(unittest.TestCase):
    def test_framework_wire_requires_one_canonical_summary(self) -> None:
        model = summary_response_model("framework")
        schema = model.model_json_schema()
        expected = {"summary", "stereotype", "questions"}
        self.assertEqual(expected, set(schema["required"]))
        self.assertFalse(schema["additionalProperties"])
        normalized = normalize_summary_response(model.model_validate(_summary()))
        self.assertIn("주문을 조회하고", normalized.summary)
        self.assertNotIn("SIGNATURE:", normalized.summary)

        for missing in expected:
            with self.subTest(missing=missing), self.assertRaises(ValidationError):
                model.model_validate({
                    key: value for key, value in _summary().items() if key != missing
                })
        for unexpected in ("unexpected",):
            with self.subTest(unexpected=unexpected), self.assertRaises(ValidationError):
                model.model_validate(_summary(**{unexpected: "value"}))

    def test_strategy_owns_stereotype_enum(self) -> None:
        framework = summary_response_model("framework").model_json_schema()
        dbms = summary_response_model("dbms").model_json_schema()
        self.assertIn("Query", framework["properties"]["stereotype"]["enum"])
        self.assertNotIn("DataQuery", framework["properties"]["stereotype"]["enum"])
        self.assertIn("DataQuery", dbms["properties"]["stereotype"]["enum"])
        self.assertNotIn("Query", dbms["properties"]["stereotype"]["enum"])

    def test_batch_requires_exact_node_keys_and_canonical_summary(self) -> None:
        keys = ("1-2", "3-4")
        model = batch_summary_response_model(keys, "dbms")
        payload = {
            "nodes": {
                key: _summary(stereotype="DataQuery")
                for key in keys
            }
        }
        output = normalize_batch_summary_response(model.model_validate(payload), keys)
        self.assertEqual(list(keys), [item.node_key for item in output.nodes])
        self.assertTrue(all(item.summary for item in output.nodes))

        for mutate in ("missing", "extra", "unexpected"):
            broken = {"nodes": {key: dict(value) for key, value in payload["nodes"].items()}}
            if mutate == "missing":
                broken["nodes"].pop("3-4")
            elif mutate == "extra":
                broken["nodes"]["9-9"] = dict(broken["nodes"]["1-2"])
            else:
                broken["nodes"]["1-2"]["unexpected"] = "model must not own this"
            with self.subTest(mutate=mutate), self.assertRaises(ValidationError):
                model.model_validate(broken)

    def test_batch_key_contract_rejects_empty_or_duplicate_input(self) -> None:
        for keys in ((), ("",), ("1-2", "1-2")):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                batch_summary_response_model(keys, "dbms")


if __name__ == "__main__":
    unittest.main()
