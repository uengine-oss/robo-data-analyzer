from __future__ import annotations

import csv
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.config.settings import AnalysisRunOutputSettings


REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "specs" / "076-shared-layout"


class SharedLayoutContractTest(unittest.TestCase):
    def test_declared_runtime_moves_resolve_to_one_implementation(self) -> None:
        with (SPEC / "move-map.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        runtime_rows = [row for row in rows if row["owner"] != "generated runtime output"]
        self.assertEqual(5, len(runtime_rows))
        expected_targets = {
            "shared/config/settings.py",
            "shared/events",
            "shared/files/cache.py",
            "api/source_resolver.py",
            "llm/tracing.py",
        }
        self.assertEqual(expected_targets, {row["new_path"] for row in runtime_rows})
        for row in runtime_rows:
            with self.subTest(old_path=row["old_path"]):
                old = REPO / row["old_path"]
                old_sources = [old] if old.is_file() else list(old.glob("*.py"))
                self.assertEqual([], old_sources)
                self.assertTrue((REPO / row["new_path"]).exists())

    def test_generated_output_default_and_marker_are_renamed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Path("output"), AnalysisRunOutputSettings().dir)
        executor = (REPO / "pipeline/execution/run_executor.py").read_text(encoding="utf-8")
        self.assertIn('.robo-data-analyzer-output', executor)

    def test_final_audit_ledger_resolves(self) -> None:
        with (SPEC / "final-audit-ledger.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertTrue(rows)
        self.assertEqual(len(rows), len({row["path"] for row in rows}))
        for row in rows:
            with self.subTest(path=row["path"], status=row["status"]):
                target = REPO / row["path"]
                exists = target.is_file() or (
                    target.is_dir()
                    and any(
                        path.is_file() and "__pycache__" not in path.parts
                        for path in target.rglob("*")
                    )
                )
                self.assertEqual("verified" == row["status"], exists)


if __name__ == "__main__":
    unittest.main()
