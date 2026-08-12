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
        "evidence_status": "sufficient",
        "missing_context": [],
        "questions": [],
    }
    value.update(overrides)
    return value


class SummaryResponseSchemaTest(unittest.TestCase):
    def test_framework_wire_requires_one_canonical_summary(self) -> None:
        model = summary_response_model("framework")
        schema = model.model_json_schema()
        expected = {
            "summary", "stereotype", "evidence_status", "missing_context", "questions",
        }
        item_schema = schema["properties"]["result"]
        branches = [
            schema["$defs"][item["$ref"].rsplit("/", 1)[-1]]
            for item in item_schema["oneOf"]
        ]
        self.assertTrue(all(expected == set(branch["required"]) for branch in branches))
        self.assertTrue(all(branch["additionalProperties"] is False for branch in branches))
        normalized = normalize_summary_response(model.model_validate({"result": _summary()}))
        self.assertIn("주문을 조회하고", normalized.summary)
        self.assertNotIn("SIGNATURE:", normalized.summary)

        for missing in expected:
            with self.subTest(missing=missing), self.assertRaises(ValidationError):
                model.model_validate({
                    "result": {
                        key: value for key, value in _summary().items() if key != missing
                    },
                })
        for unexpected in ("unexpected",):
            with self.subTest(unexpected=unexpected), self.assertRaises(ValidationError):
                model.model_validate({"result": _summary(**{unexpected: "value"})})

    def test_strategy_owns_stereotype_enum(self) -> None:
        framework = summary_response_model("framework").model_json_schema()
        dbms = summary_response_model("dbms").model_json_schema()
        def stereotypes(schema):
            item_schema = schema["properties"]["result"]
            branches = [
                schema["$defs"][item["$ref"].rsplit("/", 1)[-1]]
                for item in item_schema["oneOf"]
            ]
            return next(
                branch["properties"]["stereotype"]["enum"]
                for branch in branches
                if "enum" in branch["properties"]["stereotype"]
            )
        self.assertIn("Query", stereotypes(framework))
        self.assertNotIn("DataQuery", stereotypes(framework))
        self.assertIn("DataQuery", stereotypes(dbms))
        self.assertNotIn("Query", stereotypes(dbms))

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

    def test_insufficient_is_a_valid_non_fabricating_terminal_output(self) -> None:
        model = summary_response_model("framework")
        normalized = normalize_summary_response(model.model_validate({"result": _summary(
            summary="", stereotype="", evidence_status="insufficient",
            missing_context=["호출 대상의 계약 원문"],
        )}))
        self.assertEqual("insufficient", normalized.evidence_status)
        self.assertEqual("", normalized.summary)
        with self.assertRaises(ValidationError):
            normalize_summary_response(model.model_validate({"result": _summary(
                summary="추측한 동작", stereotype="Helper", evidence_status="insufficient",
                missing_context=["호출 대상의 계약 원문"],
            )}))

    def test_batch_key_contract_rejects_empty_or_duplicate_input(self) -> None:
        for keys in ((), ("",), ("1-2", "1-2")):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                batch_summary_response_model(keys, "dbms")


if __name__ == "__main__":
    unittest.main()
