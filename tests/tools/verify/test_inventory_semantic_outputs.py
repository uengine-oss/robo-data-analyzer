from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.inventory_semantic_outputs import (
    _prompt_inventory,
    _rule_evidence,
    _run_identity,
    _split_coordinate_groups,
)
from pipeline.models.rule_materialization import materialize_rule_items


class PromptSemanticInventoryTest(unittest.TestCase):
    def test_run_identity_is_read_from_manifest_and_database_is_pinned(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            (run_dir / "manifest.json").write_text(json.dumps({
                "analysis_scope_id": "analysis-scope/v2:abc",
                "generation_id": "generation-1",
                "neo4j_database": "framework",
            }), encoding="utf-8")

            self.assertEqual(
                ("analysis-scope/v2:abc", "generation-1"),
                _run_identity(run_dir, "framework"),
            )
            with self.assertRaisesRegex(ValueError, "database mismatch"):
                _run_identity(run_dir, "dbms")

    def test_current_rule_inventory_reads_llm_zero_structure_artifact(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            directory = run_dir / "rule-structures"
            directory.mkdir()
            record = {
                "schema": "deterministic-rule/v1",
                "target_id": "code:order.c:validate",
                "evidence": {
                    "conditions": {"C1": "ready"},
                    "effects": {},
                    "rows": {"R1": {"role": "decision", "when": ["C1"]}},
                },
                "rules": [{
                    "anchor_line": 1,
                    "structural_condition": 'F("ready")',
                    "structural_effects": [],
                    "business_effects": [],
                }],
            }
            (directory / "validate.json").write_text(
                json.dumps(record), encoding="utf-8",
            )

            prompt_dir = run_dir / "llm-prompts"
            prompt_dir.mkdir()
            rule_response = {
                "target_id": "code:order.c:validate",
                "response": {"parsed": {
                    "rows": {"R1": "business_rule"},
                    "effects": {},
                }},
            }
            (prompt_dir / "code_rule_meanings.jsonl").write_text(
                json.dumps(rule_response) + "\n", encoding="utf-8",
            )

            inventory, _, _ = _prompt_inventory(run_dir)
            evidence = _rule_evidence(run_dir)

        self.assertEqual(1, inventory["rule"]["model_calls"])
        self.assertEqual(1, inventory["rule"]["obligation_rows"])
        self.assertEqual(1, inventory["rule"]["business_rule_rows"])
        self.assertEqual(1, inventory["rule"]["empty_structural_effect_rows"])
        self.assertEqual(0, inventory["rule"]["missing_response_rows"])
        self.assertEqual(
            {"code:order.c:validate::R-1"},
            set(evidence),
        )

    def test_parent_only_nested_rule_is_accounted_without_a_model_call(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            directory = run_dir / "rule-structures"
            directory.mkdir()
            (directory / "validate.json").write_text(json.dumps({
                "schema": "deterministic-rule/v1",
                "target_id": "code:order.c:validate",
                "evidence": {
                    "conditions": {"C1": "outer"},
                    "effects": {},
                    "rows": {"R1": {
                        "role": "decision", "when": ["C1"], "direct": [],
                        "nested_control": True,
                    }},
                },
                "rules": [{
                    "anchor_line": 1,
                    "structural_condition": 'F("outer")',
                    "structural_effects": [],
                    "business_effects": [],
                }],
            }), encoding="utf-8")
            (run_dir / "llm-prompts").mkdir()

            inventory, _, _ = _prompt_inventory(run_dir)

        self.assertEqual(0, inventory["rule"]["model_calls"])
        self.assertEqual(1, inventory["rule"]["obligation_rows"])
        self.assertEqual(0, inventory["rule"]["model_obligation_rows"])
        self.assertEqual(1, inventory["rule"]["deterministic_excluded_rows"])
        self.assertEqual(1, inventory["rule"]["implementation_detail_rows"])
        self.assertEqual(0, inventory["rule"]["missing_response_rows"])

    def test_historical_assigned_parent_response_is_separated_from_current_boundary(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            directory = run_dir / "rule-structures"
            directory.mkdir()
            evidence = {
                "conditions": {"C1": "outer", "C2": "inner"},
                "effects": {},
                "rows": {
                    "R1": {
                        "role": "decision", "when": ["C1"], "direct": [],
                        "nested_control": True,
                    },
                    "R2": {"role": "decision", "when": ["C1", "C2"]},
                },
            }
            (directory / "validate.json").write_text(json.dumps({
                "schema": "deterministic-rule/v1",
                "target_id": "code:order.c:validate",
                "evidence": evidence,
                "rules": [
                    rule.model_dump(mode="json")
                    for rule in materialize_rule_items(evidence, (1, 2))
                ],
            }), encoding="utf-8")
            prompt_dir = run_dir / "llm-prompts"
            prompt_dir.mkdir()
            response = {
                "target_id": "code:order.c:validate",
                "response": {"parsed": {
                    "rows": {"R1": "business_rule", "R2": "business_rule"},
                    "effects": {},
                }},
                "response_schema": {"schema": {
                    "$defs": {"Rows": {"required": ["R1", "R2"]}},
                    "properties": {"rows": {"$ref": "#/$defs/Rows"}},
                }},
            }
            (prompt_dir / "code_rule_meanings.jsonl").write_text(
                json.dumps(response) + "\n", encoding="utf-8",
            )

            inventory, _, _ = _prompt_inventory(run_dir)

        self.assertEqual(1, inventory["rule"]["response_rows"])
        self.assertEqual(1, inventory["rule"]["business_rule_rows"])
        self.assertEqual(1, inventory["rule"]["deterministic_excluded_rows"])
        self.assertEqual(1, inventory["rule"]["legacy_deterministic_response_rows"])
        self.assertEqual(0, inventory["rule"]["missing_response_rows"])
        self.assertEqual(0, inventory["rule"]["extra_response_rows"])

    def test_current_exact_slot_summary_table_and_column_wire_are_counted(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            prompt_dir = run_dir / "llm-prompts"
            prompt_dir.mkdir()
            rule_directory = run_dir / "rule-structures"
            rule_directory.mkdir()
            rule_record = {
                "schema": "deterministic-rule/v1",
                "target_id": "code:f",
                "evidence": {
                    "conditions": {"C1": "ready"},
                    "effects": {},
                    "rows": {"R1": {"role": "decision", "when": ["C1"]}},
                },
                "rules": [{
                    "anchor_line": 1,
                    "structural_condition": 'F("ready")',
                    "structural_effects": [],
                    "business_effects": [],
                }],
            }
            (rule_directory / "f.json").write_text(
                json.dumps(rule_record), encoding="utf-8",
            )
            summary = {
                "target_id": "code:f",
                "response": {"parsed": {
                    "summary": "전체 흐름", "stereotype": "Util",
                    "slots": {"W-summary": "입력을 검증한다"},
                }},
            }
            table = {
                "target_id": "db:T",
                "response": {"parsed": {
                    "logical_name": "테이블",
                    "description": "직접 역할",
                    "columns": {"C": {
                        "logical_name": "",
                        "description": "필터 조건",
                    }},
                }},
            }
            (prompt_dir / "code_summary.jsonl").write_text(
                json.dumps(summary, ensure_ascii=False) + "\n", encoding="utf-8",
            )
            (prompt_dir / "table_description.jsonl").write_text(
                json.dumps(table, ensure_ascii=False) + "\n", encoding="utf-8",
            )

            inventory, table_ids, column_ids = _prompt_inventory(run_dir)

        self.assertEqual(0, inventory["summary"]["empty_summary"])
        self.assertEqual(1, inventory["summary"]["slots"])
        self.assertEqual(0, inventory["summary"]["empty_slots"])
        self.assertEqual(0, inventory["table"]["empty_meaning"])
        self.assertEqual(0, inventory["column"]["empty_meaning"])
        self.assertEqual(1, inventory["column"]["empty_meaning_fields"])
        self.assertEqual({"db:T"}, table_ids)
        self.assertEqual({"db:T.C"}, column_ids)

    def test_nested_same_line_nodes_are_not_reported_as_duplicate_coordinates(self) -> None:
        nested_group = {
            "file": "x.sql", "start": 25, "end": 25,
            "nodes": [{"id": "outer"}, {"id": "inner"}],
        }
        sibling_group = {
            "file": "x.sql", "start": 30, "end": 30,
            "nodes": [{"id": "left"}, {"id": "right"}],
        }

        duplicates, nested = _split_coordinate_groups(
            [nested_group, sibling_group], [("outer", "inner")],
        )

        self.assertEqual([sibling_group], duplicates)
        self.assertEqual([nested_group], nested)


if __name__ == "__main__":
    unittest.main()
