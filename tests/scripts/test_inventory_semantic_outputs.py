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
                    "slots": {
                        "W-table-name": "테이블",
                        "W-table-description": "직접 역할",
                    },
                    "columns": {"C": {
                        "slots": {
                            "W-column-name": "",
                            "W-column-description": "필터 조건",
                        },
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
        self.assertEqual(1, inventory["column"]["empty_slots"])
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
