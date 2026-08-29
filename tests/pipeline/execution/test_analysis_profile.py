from __future__ import annotations

import hashlib
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
        return data, root / "runs"

    def test_relative_paths_selection_validation_refs_and_explicit_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data, _ = self._tree(root)
            baseline = root / "baseline.json"
            answer = root / "answer.json"
            baseline.write_text("baseline", encoding="utf-8")
            answer.write_text("answer", encoding="utf-8")
            baseline_hash = hashlib.sha256(baseline.read_bytes()).hexdigest()
            answer_hash = hashlib.sha256(answer.read_bytes()).hexdigest()
            profile_path = root / "profile.yaml"
            profile_path.write_text(
                "\n".join((
                    "name: selected-skt",
                    "data_dir: corpus",
                    "strategy: framework",
                    "neo4j_database: test",
                    "locale: ko",
                    "run_output: runs",
                    "selected_source_ids:",
                    "  - z/b.c",
                    "  - a.c",
                    "validation_baseline_ref: baseline.json",
                    f"validation_baseline_sha256: {baseline_hash}",
                    "validation_answer_key_ref: answer.json",
                    f"validation_answer_key_sha256: {answer_hash}",
                )),
                encoding="utf-8",
            )

            profile = load_analysis_profile(profile_path)
            resolved = resolve_analysis_inputs(
                profile=profile,
                explicit_data_dir=None,
                explicit_strategy="dbms",
                explicit_database=None,
                explicit_locale="en-US",
                explicit_run_output=None,
                default_data_dir=root / "unused-data",
                default_database="unused",
                default_locale="fr",
                default_run_output=root / "unused-runs",
            )

            self.assertEqual(data, resolved.data_dir)
            self.assertEqual("dbms", resolved.strategy)
            self.assertEqual("en-US", resolved.locale)
            self.assertEqual(("a.c", "z/b.c"), resolved.selected_source_ids)
            self.assertEqual(baseline, resolved.validation_baseline_ref)
            self.assertEqual(answer, resolved.validation_answer_key_ref)
            rendered = "\n".join(preflight_lines(resolved))
            self.assertIn("selected_source_ids: ['a.c', 'z/b.c']", rendered)
            self.assertIn(f"validation_baseline: {baseline}", rendered)

    def test_validation_references_may_be_complete_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._tree(root)
            baseline = root / "baseline-run"
            answer = root / "gold"
            baseline.mkdir()
            answer.mkdir()
            (baseline / "manifest.json").write_text("baseline", encoding="utf-8")
            (answer / "routines.json").write_text("answer", encoding="utf-8")
            profile_path = root / "profile.yaml"
            profile_path.write_text(
                "\n".join((
                    "data_dir: corpus",
                    "strategy: framework",
                    "neo4j_database: test",
                    "locale: ko",
                    "validation_baseline_ref: baseline-run",
                    f"validation_baseline_sha256: {'a' * 64}",
                    "validation_answer_key_ref: gold",
                    f"validation_answer_key_sha256: {'b' * 64}",
                )),
                encoding="utf-8",
            )

            profile = load_analysis_profile(profile_path)

            self.assertEqual(baseline, profile.validation_baseline_ref)
            self.assertEqual(answer, profile.validation_answer_key_ref)

    def test_unknown_missing_system_and_unpaired_hash_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._tree(root)
            cases = {
                "unknown": (
                    "data_dir: corpus\nstrategy: framework\nneo4j_database: test\n"
                    "locale: ko\ncustomer: special\n"
                ),
                "missing": "data_dir: corpus\nstrategy: framework\n",
                "system": (
                    "data_dir: corpus\nstrategy: framework\nneo4j_database: system\n"
                    "locale: ko\n"
                ),
                "unpaired": (
                    "data_dir: corpus\nstrategy: framework\nneo4j_database: test\n"
                    "locale: ko\nvalidation_baseline_ref: baseline.json\n"
                ),
            }
            for name, body in cases.items():
                with self.subTest(name=name):
                    path = root / f"{name}.yaml"
                    path.write_text(body, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_analysis_profile(path)

    def test_missing_source_fails_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaisesRegex(ValueError, "source directory"):
                resolve_analysis_inputs(
                    profile=None,
                    explicit_data_dir=root / "missing",
                    explicit_strategy="framework",
                    explicit_database="test",
                    explicit_locale="ko",
                    explicit_run_output=root / "runs",
                    default_data_dir=root / "unused",
                    default_database="unused",
                    default_locale="ko",
                    default_run_output=root / "unused-runs",
                )


if __name__ == "__main__":
    unittest.main()
