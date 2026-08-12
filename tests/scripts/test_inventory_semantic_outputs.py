from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.inventory_semantic_outputs import (
    _prompt_inventory,
    _split_coordinate_groups,
)


class PromptSemanticInventoryTest(unittest.TestCase):
    def test_current_nested_summary_table_and_column_wire_are_counted(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            prompt_dir = run_dir / "llm-prompts"
            prompt_dir.mkdir()
            summary = {
                "target_id": "code:f",
                "response": {"parsed": {"result": {
                    "summary": "전체 흐름", "stereotype": "Util",
                    "evidence_status": "sufficient", "missing_context": [],
                    "questions": [],
                }}},
            }
            table = {
                "target_id": "db:T",
                "response": {"parsed": {
                    "name": "T",
                    "meaning": {
                        "logical_name": "테이블", "description": "직접 역할",
                        "evidence_status": "sufficient", "missing_context": [],
                    },
                    "columns": [{
                        "name": "C",
                        "meaning": {
                            "logical_name": "", "description": "필터 조건",
                            "evidence_status": "sufficient", "missing_context": [],
                        },
                    }],
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
        self.assertEqual({"sufficient": 1}, inventory["summary"]["status"])
        self.assertEqual(0, inventory["table"]["empty_meaning"])
        self.assertEqual(0, inventory["column"]["empty_meaning"])
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
