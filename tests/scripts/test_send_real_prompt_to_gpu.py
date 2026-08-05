from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import Mock

from scripts.send_real_prompt_to_gpu import (
    _configure_utf8_console,
    _extract_raw_text,
    _recorded_response_schema,
    _resolve_replay_method,
    _validate_raw_against_schema,
)


class RecordedResponseSchemaTest(unittest.TestCase):
    def test_windows_console_is_reconfigured_without_lossy_error_mode(self) -> None:
        stream = Mock()
        _configure_utf8_console(stream)
        stream.reconfigure.assert_called_once_with(encoding="utf-8")

    def test_integrity_checked_schema_is_returned_exactly(self) -> None:
        schema = {
            "type": "object",
            "properties": {"R1": {"type": "string"}},
            "required": ["R1"],
            "additionalProperties": False,
        }
        canonical = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        record = {"response_schema": {
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "bytes": len(canonical),
            "schema": schema,
        }}

        self.assertIs(schema, _recorded_response_schema(record))

    def test_missing_or_tampered_schema_is_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "response_schema evidence"):
            _recorded_response_schema({})
        with self.assertRaisesRegex(SystemExit, "fingerprint/bytes"):
            _recorded_response_schema({"response_schema": {
                "sha256": "0" * 64,
                "bytes": 1,
                "schema": {"type": "object"},
            }})

    def test_replay_uses_recorded_method_unless_explicitly_overridden(self) -> None:
        record = {"metadata": {"structured_output_method": "function_calling"}}
        self.assertEqual(
            "function_calling",
            _resolve_replay_method(record, override=None, no_schema=False),
        )
        self.assertEqual(
            "json_schema",
            _resolve_replay_method(
                record, override="json_schema", no_schema=False,
            ),
        )

    def test_schema_replay_rejects_historical_record_without_method(self) -> None:
        with self.assertRaisesRegex(SystemExit, "structured_output_method evidence"):
            _resolve_replay_method({}, override=None, no_schema=False)
        self.assertIsNone(
            _resolve_replay_method({}, override=None, no_schema=True)
        )

    def test_unconstrained_raw_must_still_match_recorded_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"required": {"type": "string"}},
            "required": ["required"],
            "additionalProperties": False,
        }
        self.assertEqual(
            {"required": "ok"},
            _validate_raw_against_schema('{"required":"ok"}', schema),
        )
        for raw in ('{"other":"no"}', '{"required":'):
            with self.assertRaisesRegex(SystemExit, "recorded schema"):
                _validate_raw_against_schema(raw, schema)

    def test_normalized_function_call_args_are_not_reported_as_missing_raw(self) -> None:
        raw = Mock(content="", additional_kwargs={}, tool_calls=[{
            "name": "StructuredOutput",
            "args": {"required": "한글 값"},
        }])
        self.assertEqual('{"required":"한글 값"}', _extract_raw_text(raw))

    def test_provider_function_arguments_preserve_exact_string(self) -> None:
        raw = Mock(
            content="",
            additional_kwargs={"tool_calls": [{
                "function": {"arguments": '{ "required" : "value" }'},
            }]},
            tool_calls=[],
        )
        self.assertEqual('{ "required" : "value" }', _extract_raw_text(raw))



if __name__ == "__main__":
    unittest.main()
