from __future__ import annotations

import csv
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "specs" / "077-runtime-boundaries"


class RuntimeBoundaryTest(unittest.TestCase):
    def test_every_audited_runtime_folder_exists(self) -> None:
        with (SPEC / "folder-audit.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertTrue(rows)
        self.assertEqual(len(rows), len({row["path"] for row in rows}))
        for row in rows:
            with self.subTest(path=row["path"], role=row["role"]):
                self.assertTrue((REPO / row["path"]).is_dir())

    def test_every_declared_move_has_one_current_owner(self) -> None:
        with (SPEC / "move-map.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        for row in rows:
            old = REPO / row["old_path"]
            old_sources = [old] if old.is_file() else list(old.glob("*.py"))
            self.assertEqual([], old_sources, row["old_path"])
            if row["new_path"] != "-":
                self.assertTrue((REPO / row["new_path"]).exists(), row["new_path"])


if __name__ == "__main__":
    unittest.main()
