from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.execution.analysis_profile import (
    load_analysis_profile,
    preflight_lines,
    resolve_analysis_inputs,
)


class AnalysisProfileContractTest(unittest.TestCase):
    def _tree(self, root: Path) -> tuple[Path, Path]:
        data = root / "corpus"
        (data / "source").mkdir(parents=True)
        (data / "ddl").mkdir()
        output = root / "runs"
        return data, output

    def test_relative_profile_paths_and_explicit_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data, output = self._tree(root)
            profile_path = root / "profile.yaml"
            profile_path.write_text(
                "\n".join((
                    "name: reusable-example",
                    "data_dir: corpus",
                    "strategy: framework",
                    "neo4j_database: analysis_a",
                    "locale: ko",
                    "run_output: runs",
                )),
                encoding="utf-8",
            )
            profile = load_analysis_profile(profile_path)
            explicit_output = root / "explicit-runs"
            resolved = resolve_analysis_inputs(
                profile=profile,
                explicit_data_dir=None,
                explicit_strategy="dbms",
                explicit_database="analysis_b",
                explicit_locale="en-US",
                explicit_run_output=explicit_output,
                default_data_dir=root / "unused-data",
                default_database="unused",
                default_locale="fr",
                default_run_output=root / "unused-runs",
            )

            self.assertEqual(data, resolved.data_dir)
            self.assertEqual("dbms", resolved.strategy)
            self.assertEqual("analysis_b", resolved.neo4j_database)
            self.assertEqual("en-US", resolved.locale)
            self.assertEqual(explicit_output, resolved.run_output)
            rendered = "\n".join(preflight_lines(resolved))
            for expected in ("source:", "ddl:", "strategy: dbms", "neo4j_database: analysis_b",
                             "locale: en-US", "run_output:"):
                self.assertIn(expected, rendered)

    def test_profile_rejects_unknown_missing_and_dangerous_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._tree(root)
            cases = {
                "unknown": (
                    "data_dir: corpus\nstrategy: framework\nneo4j_database: a\n"
                    "locale: ko\nrun_output: runs\ncustomer: special\n"
                ),
                "missing": "data_dir: corpus\nstrategy: framework\n",
                "system": (
                    "data_dir: corpus\nstrategy: framework\nneo4j_database: system\n"
                    "locale: ko\nrun_output: runs\n"
                ),
            }
            for name, body in cases.items():
                with self.subTest(name=name):
                    path = root / f"{name}.yaml"
                    path.write_text(body, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_analysis_profile(path)

    def test_omitted_run_output_uses_existing_default_logs_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data, _ = self._tree(root)
            profile_path = root / "profile.yaml"
            profile_path.write_text(
                "\n".join((
                    "data_dir: corpus",
                    "strategy: framework",
                    "neo4j_database: analysis",
                    "locale: ko",
                )),
                encoding="utf-8",
            )
            profile = load_analysis_profile(profile_path)
            default_logs = root / "logs"
            resolved = resolve_analysis_inputs(
                profile=profile,
                explicit_data_dir=None,
                explicit_strategy=None,
                explicit_database=None,
                explicit_locale=None,
                explicit_run_output=None,
                default_data_dir=root / "unused-data",
                default_database="unused",
                default_locale="en-US",
                default_run_output=default_logs,
            )

            self.assertEqual(data, resolved.data_dir)
            self.assertIsNone(profile.run_output)
            self.assertEqual(default_logs, resolved.run_output)

    def test_missing_source_fails_during_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaisesRegex(ValueError, "source directory"):
                resolve_analysis_inputs(
                    profile=None,
                    explicit_data_dir=root / "missing",
                    explicit_strategy="framework",
                    explicit_database="analysis",
                    explicit_locale="ko",
                    explicit_run_output=root / "runs",
                    default_data_dir=root / "unused",
                    default_database="unused",
                    default_locale="ko",
                    default_run_output=root / "unused-runs",
                )


if __name__ == "__main__":
    unittest.main()
