from __future__ import annotations

import csv
import subprocess
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "specs" / "058-analyzer-structure-refactor"
REMOVED_RUNTIME_ROOTS = (
    "analysis_flow",
    "agent_tools",
    "cli_scripts",
    "data_models",
    "llm_configs",
    "prompts",
    "shared",
    "skills",
    "system",
    "log",
    "logs",
    "deploy",
)


class RefactorContractTest(unittest.TestCase):
    def test_default_data_dir_survives_settings_move(self) -> None:
        from shared.config.settings import _DEFAULT_DATA_DIR

        self.assertEqual((REPO.parent / "data").resolve(), _DEFAULT_DATA_DIR.resolve())

    def test_pipeline_llm_asset_paths_survive_moves(self) -> None:
        from agents.residual import _RESIDUAL_PROMPTS_DIR, _RESIDUAL_RULE
        from agents.scan import _SYSTEM_PROMPT_PATH

        self.assertTrue(_SYSTEM_PROMPT_PATH.is_file())
        self.assertTrue((_RESIDUAL_PROMPTS_DIR / f"{_RESIDUAL_RULE}.yaml").is_file())

    def test_frozen_baseline_has_503_unique_paths(self) -> None:
        lines = (SPEC / "audit-baseline.txt").read_text(encoding="utf-8").splitlines()
        paths = [line for line in lines if line and not line.startswith("#")]
        self.assertEqual(503, len(paths))
        self.assertEqual(len(paths), len(set(paths)))

    @unittest.skipIf(
        any((REPO / name).exists() for name in REMOVED_RUNTIME_ROOTS),
        "v10 migration is still in progress",
    )
    def test_audit_ledger_resolves_every_baseline_path(self) -> None:
        baseline = {
            line
            for line in (SPEC / "audit-baseline.txt").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        }
        with (SPEC / "audit-ledger.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        by_path = {row["baseline_path"]: row for row in rows}
        self.assertEqual(baseline, baseline & by_path.keys())
        allowed = {"fixed", "deleted", "no-violation"}
        invalid = [path for path in baseline if by_path[path]["verdict"] not in allowed]
        self.assertEqual([], invalid, f"invalid audit verdicts: {len(invalid)}")
        for path in baseline:
            row = by_path[path]
            with self.subTest(path=path, verdict=row["verdict"]):
                final_exists = (REPO / row["final_path"]).is_file()
                self.assertEqual(row["verdict"] != "deleted", final_exists)

    @unittest.skipIf(
        any((REPO / name).exists() for name in REMOVED_RUNTIME_ROOTS),
        "v10 migration is still in progress",
    )
    def test_new_files_are_audited(self) -> None:
        with (SPEC / "audit-ledger.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        covered = {row["final_path"] for row in rows}
        listed = subprocess.run(
            ["git", "-c", "core.quotePath=false", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.splitlines()
        current = {path for path in listed if path and (REPO / path).is_file()}
        missing = sorted(current - covered)
        self.assertEqual([], missing, f"unaudited new files: {missing[:10]}")

    def test_every_audit_row_has_a_final_verdict(self) -> None:
        with (SPEC / "audit-ledger.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        baseline_paths = [row["baseline_path"] for row in rows]
        self.assertEqual(
            len(baseline_paths),
            len(set(baseline_paths)),
            "audit ledger contains duplicate baseline_path rows",
        )
        allowed = {"fixed", "deleted", "no-violation"}
        invalid = [
            (row["baseline_path"], row["verdict"])
            for row in rows
            if row["verdict"] not in allowed
        ]
        self.assertEqual([], invalid, f"non-final audit verdicts: {invalid[:10]}")

    @unittest.skipIf(
        (REPO / "specs" / "076-shared-layout").exists() and (REPO / "shared").exists(),
        "legacy final tree is superseded by spec 076",
    )
    def test_move_map_resolves_each_declared_path(self) -> None:
        with (SPEC / "move-map.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        old_paths = [row["old_path"] for row in rows]
        self.assertEqual(len(old_paths), len(set(old_paths)), "move map contains duplicate old_path rows")
        allowed_actions = {"keep", "move", "delete", "delete-empty"}
        invalid_actions = [
            (row["old_path"], row["action"])
            for row in rows
            if row["action"] not in allowed_actions
        ]
        self.assertEqual([], invalid_actions, f"invalid move actions: {invalid_actions[:10]}")
        for row in rows:
            with self.subTest(old_path=row["old_path"], action=row["action"]):
                old_exists = (REPO / row["old_path"]).exists()
                if row["action"] == "keep":
                    self.assertTrue(old_exists)
                    self.assertEqual(row["old_path"], row["final_path"])
                else:
                    self.assertFalse(old_exists)
                if row["action"] == "move":
                    self.assertTrue((REPO / row["final_path"]).is_file())

    @unittest.skipIf(
        any((REPO / name).exists() for name in REMOVED_RUNTIME_ROOTS),
        "v10 migration is still in progress",
    )
    def test_old_runtime_roots_are_absent(self) -> None:
        remaining = [name for name in REMOVED_RUNTIME_ROOTS if (REPO / name).exists()]
        self.assertEqual([], remaining)


if __name__ == "__main__":
    unittest.main()
