from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.run_strategy_a_progressive import (
    CANDIDATE_VARIANT,
    CONTROL_VARIANT,
    DEFAULT_SEAL,
    PROMPT_CANDIDATE_VARIANT,
    build_request,
    build_schema,
    load_preflight,
    parse_json_no_duplicates,
    resolve_output_dir,
    summarize_repeat_stability,
    validate_response,
)


class StrategyAProgressiveRunnerTest(unittest.TestCase):
    def test_sealed_actual_stage_is_exact_and_history_is_unchanged(self) -> None:
        preflight = load_preflight(DEFAULT_SEAL)

        self.assertEqual(preflight["source_files"], 11)
        self.assertEqual(preflight["source_mismatches"], [])
        self.assertEqual(preflight["logical_records"], 15)
        self.assertEqual(preflight["logical_rows"], 104)
        self.assertEqual(preflight["gold_cases"], 18)
        self.assertEqual(preflight["selected_prompt_ledger_sha256"],
                         "75292142cb95b9980b1d3064857a517cbfbfcb4dd43ab4fbebe36a224ee4182e")
        self.assertEqual(preflight["history_mismatches"], [])

    def test_control_keeps_historical_limits_and_candidate_has_none(self) -> None:
        rows = ["R1", "R2"]
        control = build_schema(rows, CONTROL_VARIANT)
        candidate = build_schema(rows, CANDIDATE_VARIANT)

        control_leaf = control["schema"]["properties"]["rules"]["properties"]["R1"]
        candidate_leaf = candidate["schema"]["properties"]["rules"]["properties"]["R1"]
        self.assertEqual(control_leaf["properties"]["given"]["maxLength"], 500)
        self.assertEqual(control_leaf["properties"]["then_relation"]["maxLength"], 1000)
        self.assertNotIn("maxLength", candidate_leaf["properties"]["given"])
        self.assertNotIn("maxLength", candidate_leaf["properties"]["then_relation"])

    def test_request_isolates_only_wire_limit_and_output_budget(self) -> None:
        config = SimpleNamespace(
            model="current-model",
            max_tokens=30000,
            sampling=SimpleNamespace(temperature=0.1, top_p=1.0),
            extra_body={"top_k": 20},
        )
        control = build_request("payload", ["R1"], config, CONTROL_VARIANT)
        candidate = build_request("payload", ["R1"], config, CANDIDATE_VARIANT)

        self.assertEqual(control["messages"], candidate["messages"])
        self.assertEqual(control["model"], candidate["model"])
        self.assertEqual(control["temperature"], candidate["temperature"])
        self.assertEqual(control["top_p"], candidate["top_p"])
        self.assertEqual(control["top_k"], candidate["top_k"])
        self.assertEqual(control["max_tokens"], 4096)
        self.assertEqual(candidate["max_tokens"], 30000)

    def test_prompt_candidate_changes_only_the_user_contract_and_has_no_content_cap(self) -> None:
        config = SimpleNamespace(
            model="current-model",
            max_tokens=30000,
            sampling=SimpleNamespace(temperature=0.1, top_p=1.0),
            extra_body={"top_k": 20},
        )
        historical = build_request("historical contract\nresponse_locale: ko\n", ["R1"], config,
                                   CONTROL_VARIANT)
        candidate = build_request("historical contract\nresponse_locale: ko\n", ["R1"], config,
                                  PROMPT_CANDIDATE_VARIANT)

        self.assertEqual(historical["messages"][0], candidate["messages"][0])
        self.assertNotEqual(historical["messages"][1], candidate["messages"][1])
        candidate_user = candidate["messages"][1]["content"]
        self.assertIn("no_direct_effect()", candidate_user)
        self.assertIn("동일 value version", candidate_user)
        self.assertIn("symbolic", candidate_user)
        self.assertNotIn("member_authenticate", candidate_user)
        leaf = candidate["response_format"]["json_schema"]["schema"]["properties"]["rules"][
            "properties"]["R1"]
        self.assertNotIn("maxLength", leaf["properties"]["given"])
        self.assertNotIn("maxLength", leaf["properties"]["then_relation"])

    def test_prompt_candidate_requires_explicit_no_direct_effect_relation(self) -> None:
        evidence = {
            "conditions": {"C1": "ready == 1"},
            "effects": {},
            "rows": {"R1": {"role": "complement", "when": ["C1"], "direct": []}},
        }
        explicit = json.dumps({
            "rules": {"R1": {
                "given": "ready == 1",
                "then_relation": "no_direct_effect()",
                "semantic_status": "exact",
            }}
        })
        unconstrained = json.dumps({
            "rules": {"R1": {
                "given": "ready == 1",
                "then_relation": "True",
                "semantic_status": "exact",
            }}
        })

        accepted = validate_response(explicit, evidence, ["R1"], PROMPT_CANDIDATE_VARIANT)
        rejected = validate_response(unconstrained, evidence, ["R1"], PROMPT_CANDIDATE_VARIANT)

        self.assertEqual(accepted["python_parse_failures"], [])
        self.assertEqual(accepted["grounding_failures"], [])
        self.assertEqual(accepted["semantic_contract_failures"], [])
        self.assertEqual(rejected["semantic_contract_failures"], [
            {"row": "R1", "reason": "missing_no_direct_effect_relation"}
        ])

    def test_prompt_candidate_cannot_mark_unversioned_or_non_python_effects_exact(self) -> None:
        evidence = {
            "conditions": {"C1": "rc < 0", "C2": "rc < 0"},
            "effects": {
                "E1": {"op": "set", "target": "rc", "value": "RET_OK"},
                "E2": {"op": "set", "target": "rc", "value": "call(&value)"},
            },
            "rows": {"R1": {"when": ["!C1", "C2"], "direct": ["E1", "E2"]}},
        }
        response = json.dumps({
            "rules": {"R1": {
                "given": "not (rc < 0) and rc < 0",
                "then_relation": "rc == RET_OK",
                "semantic_status": "unreachable",
            }}
        })

        result = validate_response(response, evidence, ["R1"], PROMPT_CANDIDATE_VARIANT)

        reasons = {item["reason"] for item in result["semantic_contract_failures"]}
        self.assertIn("unreachable_without_value_version_proof", reasons)
        self.assertIn("multiple_writes_without_ordered_versions", reasons)
        self.assertIn("non_python_effect_without_typed_normalization", reasons)

    def test_prompt_candidate_allows_empty_affected_expression_only_when_degraded(self) -> None:
        evidence = {
            "conditions": {"C1": {"op": "source_token", "value": "flag && ready"}},
            "effects": {},
            "rows": {"R1": {"when": ["C1"], "direct": []}},
        }
        response = json.dumps({
            "rules": {"R1": {
                "given": "",
                "then_relation": "",
                "semantic_status": "needs_context",
            }}
        })

        result = validate_response(response, evidence, ["R1"], PROMPT_CANDIDATE_VARIANT)

        self.assertEqual(result["python_parse_failures"], [])
        self.assertEqual(result["semantic_contract_failures"], [])

    def test_repeat_stability_separates_text_ast_and_relation_normalization(self) -> None:
        results = []
        expressions = ["not (rc == RET_OK)", "rc != RET_OK", "not (rc == RET_OK)"]
        for repeat, given in enumerate(expressions, 1):
            content = json.dumps({
                "rules": {"R1": {
                    "given": given,
                    "then_relation": "ret == RET_FAIL",
                    "semantic_status": "exact",
                }}
            }, ensure_ascii=False, separators=(",", ":") if repeat != 2 else (", ", ": "))
            results.append({
                "variant": PROMPT_CANDIDATE_VARIANT,
                "record_key": "record-1",
                "repeat": repeat,
                "expected_rows": ["R1"],
                "raw_content": content,
            })

        summary = summarize_repeat_stability(results, expected_repeats=3)

        self.assertEqual(summary["complete_record_groups"], 1)
        self.assertEqual(summary["record_raw_content_identical"], 0)
        self.assertEqual(summary["record_canonical_json_identical"], 0)
        self.assertEqual(summary["row_text_identical"], 0)
        self.assertEqual(summary["row_python_ast_identical"], 0)
        self.assertEqual(summary["row_relation_normalized_identical"], 1)
        self.assertEqual(summary["row_status_identical"], 1)

    def test_duplicate_json_keys_fail_before_parsing(self) -> None:
        text = '{"rules":{"R1":{"given":"True"},"R1":{"given":"False"}}}'
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            parse_json_no_duplicates(text)

    def test_validation_accepts_grounded_python_relations(self) -> None:
        evidence = {
            "conditions": {"C1": {"op": "eq", "left": "ctx->member_id", "right": "member_id"}},
            "effects": {"E1": {"op": "ret", "value": "RET_OK"}},
            "rows": {"R1": {"when": ["C1"], "direct": ["E1"]}},
        }
        response = json.dumps({
            "rules": {"R1": {
                "given": "ctx.member_id == member_id",
                "then_relation": "ret == RET_OK",
                "semantic_status": "exact",
            }}
        })

        result = validate_response(response, evidence, ["R1"])

        self.assertTrue(result["json_valid"])
        self.assertTrue(result["row_coverage"])
        self.assertEqual(result["python_parse_failures"], [])
        self.assertEqual(result["grounding_failures"], [])

    def test_validation_exposes_missing_row_syntax_and_fabricated_names(self) -> None:
        evidence = {
            "conditions": {"C1": {"op": "eq", "left": "count", "right": "0"}},
            "effects": {},
            "rows": {
                "R1": {"when": ["C1"], "direct": []},
                "R2": {"when": [], "direct": []},
            },
        }
        response = json.dumps({
            "rules": {"R1": {
                "given": "invented_name == 7",
                "then_relation": "(",
                "semantic_status": "exact",
            }}
        })

        result = validate_response(response, evidence, ["R1", "R2"])

        self.assertFalse(result["row_coverage"])
        self.assertEqual(result["missing_rows"], ["R2"])
        self.assertEqual(len(result["python_parse_failures"]), 1)
        self.assertGreaterEqual(len(result["grounding_failures"]), 1)

    def test_grounding_compares_decoded_quoted_literal_value(self) -> None:
        evidence = {
            "conditions": {},
            "effects": {"E1": {"op": "set", "target": "out[0]", "value": "'\\0'"}},
            "rows": {"R1": {"when": [], "direct": ["E1"], "source": "out[0] = '\\0';"}},
        }
        response = json.dumps({
            "rules": {"R1": {
                "given": "True",
                "then_relation": "out[0] == '\\0'",
                "semantic_status": "exact",
            }}
        })

        result = validate_response(response, evidence, ["R1"])

        self.assertEqual(result["python_parse_failures"], [])
        self.assertEqual(result["grounding_failures"], [])

    def test_output_directory_is_fixed_below_sealed_root_and_must_be_new(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            output = resolve_output_dir(root, "stage1-current-r1")
            self.assertEqual(output, root / "stage1-current-r1")
            with self.assertRaisesRegex(ValueError, "run id"):
                resolve_output_dir(root, "../outside")
            output.mkdir()
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                resolve_output_dir(root, "stage1-current-r1")


if __name__ == "__main__":
    unittest.main()
