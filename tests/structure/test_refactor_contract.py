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
)


class RefactorContractTest(unittest.TestCase):
    def test_default_data_dir_survives_env_catalog_move(self) -> None:
        from system.env_catalog import _DEFAULT_DATA_DIR

        self.assertEqual((REPO.parent / "data").resolve(), _DEFAULT_DATA_DIR.resolve())

    def test_pipeline_llm_asset_paths_survive_moves(self) -> None:
        from pipeline.steps.step3_scan_project.scan_agent import _SYSTEM_PROMPT_PATH
        from pipeline.steps.step4_linking.stage3_residual_patterns.residual_pattern_loop import (
            _RESIDUAL_PROMPT_PATH,
        )
        from pipeline.steps.step5_build_context.large_code_one_liner import _PROMPTS_COMMON

        self.assertTrue(_SYSTEM_PROMPT_PATH.is_file())
        self.assertTrue(_RESIDUAL_PROMPT_PATH.is_file())
        self.assertTrue((_PROMPTS_COMMON / "large_code_one_liner.yaml").is_file())

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

    @unittest.skipIf(
        any((REPO / name).exists() for name in REMOVED_RUNTIME_ROOTS),
        "v10 migration is still in progress",
    )
    def test_old_runtime_roots_are_absent(self) -> None:
        remaining = [name for name in REMOVED_RUNTIME_ROOTS if (REPO / name).exists()]
        self.assertEqual([], remaining)


if __name__ == "__main__":
    unittest.main()
