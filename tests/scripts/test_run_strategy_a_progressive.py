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
    build_request,
    build_schema,
    load_preflight,
    parse_json_no_duplicates,
    resolve_output_dir,
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
