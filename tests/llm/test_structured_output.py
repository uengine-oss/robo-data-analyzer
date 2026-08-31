from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import jsonschema
from langchain_openai import ChatOpenAI

from llm.structured_output import (
    STRUCTURED_RESPONSE_NAME,
    _inline_local_refs,
    bind_structured_output,
    provider_structured_output_schema,
)


class StructuredOutputBindingTest(unittest.TestCase):
    def test_function_calling_is_strict_and_cannot_mutate_canonical_schema(self) -> None:
        canonical = {
            "type": "object",
            "properties": {"items": {"type": "array", "maxItems": 10}},
            "required": ["items"],
        }
        llm = MagicMock()

        def mutate(schema, **_kwargs):
            schema["properties"]["items"]["maxItems"] = 0
            return "bound"

        llm.with_structured_output.side_effect = mutate
        self.assertEqual(
            "bound",
            bind_structured_output(llm, canonical, method="function_calling"),
        )
        self.assertEqual(10, canonical["properties"]["items"]["maxItems"])
        kwargs = llm.with_structured_output.call_args.kwargs
        self.assertEqual("function_calling", kwargs["method"])
        self.assertTrue(kwargs["strict"])
        self.assertTrue(kwargs["include_raw"])

    def test_json_schema_is_explicit_without_function_strict_flag(self) -> None:
        llm = MagicMock()
        canonical = {"type": "object"}
        bind_structured_output(llm, canonical, method="json_schema")
        provider = llm.with_structured_output.call_args.args[0]
        kwargs = llm.with_structured_output.call_args.kwargs
        self.assertEqual(STRUCTURED_RESPONSE_NAME, provider["title"])
        self.assertNotIn("title", canonical)
        self.assertEqual("json_schema", kwargs["method"])
        self.assertNotIn("strict", kwargs)

    def test_all_methods_bind_title_free_schema_with_real_openai_adapter(self) -> None:
        canonical = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
        llm = ChatOpenAI(
            model="gpt-5.4-mini-2026-03-17",
            api_key="sk-test",
        )

        for method in ("provider_default", "json_schema", "function_calling"):
            with self.subTest(method=method):
                bound = bind_structured_output(llm, canonical, method=method)
                raw = bound.first.steps__["raw"]
                if method == "function_calling":
                    function = raw.kwargs["tools"][0]["function"]
                    self.assertEqual(STRUCTURED_RESPONSE_NAME, function["name"])
                    semantic_schema = function["parameters"]
                else:
                    response_format = raw.kwargs["response_format"]["json_schema"]
                    self.assertEqual(
                        STRUCTURED_RESPONSE_NAME,
                        response_format["name"],
                    )
                    semantic_schema = response_format["schema"]
                self.assertEqual(canonical, semantic_schema)
                self.assertNotIn("title", semantic_schema)

        self.assertEqual(
            canonical,
            provider_structured_output_schema(canonical, method="json_schema"),
        )

    def test_function_provider_copy_inlines_local_defs_without_mutating_canonical(self) -> None:
        canonical = {
            "$defs": {
                "Item": {
                    "type": "object",
                    "properties": {"key": {"enum": ["E1"]}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
            },
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/$defs/Item"}},
            },
            "required": ["items"],
            "additionalProperties": False,
        }
        llm = MagicMock()
        bind_structured_output(llm, canonical, method="function_calling")
        provider = llm.with_structured_output.call_args.args[0]

        self.assertIn("$defs", canonical)
        self.assertNotIn("$defs", provider)
        self.assertNotIn("$ref", repr(provider))
        for payload in ({"items": [{"key": "E1"}]}, {"items": []}):
            jsonschema.validate(payload, canonical)
            jsonschema.validate(payload, provider)
        for payload in ({"items": [{"key": "bad"}]}, {"items": [{"extra": 1}]}):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(payload, canonical)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(payload, provider)

    def test_local_ref_failures_are_explicit(self) -> None:
        cases = (
            ({"$ref": "#/$defs/Missing"}, "unresolved"),
            ({"$defs": {"Loop": {"$ref": "#/$defs/Loop"}},
              "$ref": "#/$defs/Loop"}, "recursive"),
            ({"$defs": {"Item": {"type": "string"}},
              "type": "object", "properties": {
                  "item": {"$ref": "#/$defs/Item", "description": "sibling"}
              }}, "sibling"),
            ({"$ref": "https://example.invalid/schema"}, "local schema ref"),
        )
        for schema, message in cases:
            with self.subTest(schema=schema):
                with self.assertRaisesRegex(ValueError, message):
                    _inline_local_refs(schema)

    def test_unknown_method_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported structured"):
            bind_structured_output(MagicMock(), {}, method="typo")


if __name__ == "__main__":
    unittest.main()
