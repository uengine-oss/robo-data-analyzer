from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import Mock

from scripts.send_real_prompt_to_gpu import (
    _configure_utf8_console,
    _current_fixed_contract,
    _current_contract_response_schema,
    _current_summary_response_schema,
    _extract_raw_text,
    _format_parsed_payload,
    _recorded_response_schema,
    _replace_recorded_summary_contract,
    _replace_recorded_fixed_contract,
    _upgrade_recorded_input_markers,
    _replace_framework_summary_envelope,
    _resolve_replay_method,
    _select_replay_record,
    _validate_raw_against_schema,
)


class RecordedResponseSchemaTest(unittest.TestCase):
    def test_current_framework_summary_schema_has_sufficiency_fields(self) -> None:
        schema = _current_summary_response_schema("code_summary", "fixed evidence")
        expected = {
            "summary", "stereotype", "evidence_status", "missing_context", "questions",
        }
        result = schema["properties"]["result"]
        self.assertEqual("evidence_status", result["discriminator"]["propertyName"])
        for variant in result["oneOf"]:
            branch = schema["$defs"][variant["$ref"].rsplit("/", 1)[-1]]
            self.assertTrue(expected.issubset(branch["required"]))

    def test_current_dbms_summary_schema_restores_recorded_node_keys(self) -> None:
        schema = _current_summary_response_schema(
            "code_summary_batch", "required_node_keys: 1-2, 8-13\n",
        )
        nodes = schema["properties"]["nodes"]
        if "$ref" in nodes:
            nodes = schema["$defs"][nodes["$ref"].rsplit("/", 1)[-1]]
        self.assertEqual({"1-2", "8-13"}, set(nodes["required"]))

    def test_framework_replay_replaces_single_summary_envelope(self) -> None:
        old = "이 요청은 `summary`, `stereotype`, `questions` 세 필드만 출력한다."
        replaced = _replace_framework_summary_envelope(old)
        self.assertIn("최상위 `result`", replaced)
        self.assertIn("`missing_context`", replaced)

        self.assertEqual(replaced, _replace_framework_summary_envelope(replaced))
        multiline = replaced.replace("`stereotype`, ", "`stereotype`,\n")
        self.assertEqual(multiline, _replace_framework_summary_envelope(multiline))

    def test_replay_selection_requires_a_unique_target_and_node_key(self) -> None:
        records = [
            {
                "target_id": "code:routine",
                "user_content": "required_node_keys: 10-12, 11-11\n",
            },
            {
                "target_id": "code:routine",
                "user_content": "required_node_keys: 1-24\n",
            },
        ]
        selected = _select_replay_record(
            records, target="code:routine", node_key="11-11", index=0,
        )
        self.assertEqual("10-12, 11-11", selected["user_content"].split(": ")[1].strip())
        with self.assertRaisesRegex(SystemExit, "2건"):
            _select_replay_record(
                records, target="code:routine", node_key=None, index=0,
            )
        with self.assertRaisesRegex(SystemExit, "0건"):
            _select_replay_record(
                records, target="code:routine", node_key="missing", index=0,
            )

    def test_parsed_replay_payload_is_rendered_as_lossless_utf8_json(self) -> None:
        rendered = _format_parsed_payload({
            "summary": "외부 검증에 위임한다.",
            "questions": [],
        })
        self.assertIn('"summary": "외부 검증에 위임한다."', rendered)
        self.assertNotIn("\\u", rendered)
        self.assertEqual(
            {
                "summary": "외부 검증에 위임한다.",
                "questions": [],
            },
            json.loads(rendered),
        )

    def test_current_summary_contract_replaces_exactly_one_recorded_section(
        self,
    ) -> None:
        old = """prefix
## summary — 목적 헤더 계약

old contract

## stereotype — 역할 분류 계약
suffix
"""
        current = """## [INPUT GUIDE — 각 값의 뜻·권한·허용 판정]

new contract
"""
        replaced = _replace_recorded_summary_contract(old, current)
        self.assertEqual(1, replaced.count("new contract"))
        self.assertNotIn("old contract", replaced)
        self.assertIn("prefix", replaced)
        self.assertIn("## stereotype — 역할 분류 계약\nsuffix", replaced)

        already_current = old.replace(
            "## summary — 목적 헤더 계약", current.splitlines()[0],
        ).replace(
            "## stereotype — 역할 분류 계약",
            "## [DECISION — stereotype 역할 분류]",
        )
        replaced_current = _replace_recorded_summary_contract(
            already_current, current,
        )
        self.assertEqual(1, replaced_current.count("new contract"))
        self.assertNotIn("old contract", replaced_current)

    def test_current_summary_contract_rejects_ambiguous_or_wrong_sections(
        self,
    ) -> None:
        current = "## [INPUT GUIDE — 각 값의 뜻·권한·허용 판정]\n\nnew\n"
        for recorded in (
            "no summary section",
            "## summary — 목적 헤더 계약\none",
            (
                "## summary — 목적 헤더 계약\none\n"
                "## stereotype — 역할 분류 계약\n"
                "## summary — 목적 헤더 계약\ntwo\n"
                "## stereotype — 역할 분류 계약\n"
            ),
        ):
            with self.subTest(recorded=recorded):
                with self.assertRaises(SystemExit):
                    _replace_recorded_summary_contract(recorded, current)

        with self.assertRaisesRegex(SystemExit, "current summary contract"):
            _replace_recorded_summary_contract(
                "## summary — 목적 헤더 계약\nold\n"
                "## stereotype — 역할 분류 계약\n",
                "wrong heading",
            )

    def test_current_fixed_contract_preserves_recorded_input_bytes(self) -> None:
        recorded = "old fixed\n# [INPUT — packet]\nvalue: {opaque}\n"
        replaced = _replace_recorded_fixed_contract(recorded, "new fixed\n")
        self.assertEqual("new fixed\n# [INPUT — packet]\nvalue: {opaque}\n", replaced)
        self.assertNotIn("old fixed", replaced)

        current = _current_fixed_contract("code_table_insights_batch")
        self.assertIn("한 번만 생성", current)
        self.assertNotIn("# [INPUT —", current)

    def test_current_contract_upgrades_child_summary_authority_marker(self) -> None:
        recorded = (
            "35-55 | [CONTEXT_SUMMARY child_range=35-55 "
            "evidence_status=sufficient missing_context=[]] IF: candidate"
        )
        upgraded = _upgrade_recorded_input_markers(recorded)
        self.assertIn(
            "[CHILD_SUMMARY_CANDIDATE child_range=35-55 authority=non_source ",
            upgraded,
        )
        self.assertNotIn("[CONTEXT_SUMMARY", upgraded)

    def test_current_fixed_contract_rejects_ambiguous_input_boundaries(self) -> None:
        for recorded in ("no boundary", "# [INPUT — one]\n# [INPUT — two]\n"):
            with self.subTest(recorded=recorded):
                with self.assertRaises(SystemExit):
                    _replace_recorded_fixed_contract(recorded, "fixed\n")

    def test_current_table_usage_schema_removes_only_intermediate_columns(self) -> None:
        schema = {
            "$defs": {
                "AssignedColumnUsageMap_x": {
                    "title": "AssignedColumnUsageMap_x", "type": "object",
                },
                "AssignedTableInsightValue_x": {
                    "title": "AssignedTableInsightValue_x",
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "usage_summary": {"type": "string"},
                        "columns": {"$ref": "#/$defs/AssignedColumnUsageMap_x"},
                    },
                    "required": ["role", "usage_summary", "columns"],
                },
            },
            "type": "object",
            "properties": {
                "table_insights": {"$ref": "#/$defs/AssignedTableInsightValue_x"},
            },
        }
        canonical = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        record = {"response_schema": {
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "bytes": len(canonical),
            "schema": schema,
        }}
        current = _current_contract_response_schema("code_table_insights", record)
        item = current["$defs"]["AssignedTableInsightValue_x"]
        self.assertEqual(["role", "usage_summary"], item["required"])
        self.assertNotIn("columns", item["properties"])
        self.assertNotIn("AssignedColumnUsageMap_x", current["$defs"])

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
