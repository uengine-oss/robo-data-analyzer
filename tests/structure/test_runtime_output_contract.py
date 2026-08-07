"""spec 061 — runtime event code and generated run output have distinct owners."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.config.settings import (
    AnalysisRunOutputSettings,
    EmbeddingSettings,
    RuntimeEventSettings,
    SemanticSearchSettings,
    _getenv_bool,
)
from pipeline.models.analysis_meta import AnalysisRun, Codebase
from pipeline.execution.run_executor import _reset_run_output_root
from shared.events.runtime_logger import _level_from_name


REPO = Path(__file__).resolve().parents[2]


class RuntimeOutputContractTest(unittest.TestCase):
    def test_default_setting_and_analysis_run_use_per_run_directory(self) -> None:
        # 2026-08-07 개편: 루트는 logs 그대로, 산출물은 logs/<run_id>/ 에 격리된다.
        # run 끼리 서로의 증거를 지우지 않아야 전후 비교가 백업 없이 성립한다.
        with patch.dict(os.environ, {"ROBO_RUN_OUTPUT_DIR": "./logs"}, clear=False):
            settings = AnalysisRunOutputSettings()
        self.assertEqual(Path("./logs"), settings.dir)

        run = AnalysisRun.now(Codebase(root=REPO, strategy="framework"), settings.dir)
        self.assertEqual(Path("./logs") / run.run_id, run.run_dir)

    def test_reset_prepares_run_directory_under_owned_root(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "logs"
            run = AnalysisRun.now(Codebase(root=REPO, strategy="framework"), root)
            _reset_run_output_root(root, run.run_dir)
            self.assertTrue((root / ".robo-data-analyzer-output").is_file())
            self.assertTrue((run.run_dir / "llm-prompts").is_dir())

            # 개편 전 평면 산출물은 소유 표식 아래에서 제거된다 — run 폴더는 보존.
            sentinel = root / "stale.json"
            sentinel.write_text("previous-run", encoding="utf-8")
            second = AnalysisRun.now(Codebase(root=REPO, strategy="framework"), root)
            _reset_run_output_root(root, second.run_dir)
            self.assertFalse(sentinel.exists())
            self.assertTrue(run.run_dir.is_dir())
            self.assertTrue((second.run_dir / "llm-prompts").is_dir())

    def test_reset_refuses_nonempty_unowned_directory_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "not-analyzer-output"
            root.mkdir()
            sentinel = root / "user-file.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "소유하지 않은"):
                _reset_run_output_root(root)

            self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))

    def test_reset_rejects_invalid_marker_and_non_directory_path(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "output"
            root.mkdir()
            marker = root / ".robo-data-analyzer-output"
            marker.write_text("another-owner\n", encoding="utf-8")
            sentinel = root / "result.json"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "표식이 올바르지"):
                _reset_run_output_root(root)
            self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))

            file_path = Path(parent) / "output-file"
            file_path.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "디렉터리가 아닙니다"):
                _reset_run_output_root(file_path)
            self.assertEqual("preserve", file_path.read_text(encoding="utf-8"))

    def test_reset_refuses_even_legacy_shaped_tree_without_explicit_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "output"
            (root / "analysis" / "latest").mkdir(parents=True)
            (root / "llm-prompts" / "latest").mkdir(parents=True)
            (root / "search").mkdir()
            sentinel = root / "analysis" / "latest" / "result.json"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "소유하지 않은"):
                _reset_run_output_root(root)

            self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))

    def test_invalid_environment_values_fail_closed(self) -> None:
        with patch.dict(os.environ, {"ROBO_CACHE_ENABLED": "treu"}, clear=False):
            with self.assertRaisesRegex(ValueError, "ROBO_CACHE_ENABLED"):
                _getenv_bool("ROBO_CACHE_ENABLED", False)

        with self.assertRaisesRegex(ValueError, "dense_impl"):
            SemanticSearchSettings(dense_impl="indx")
        with self.assertRaisesRegex(ValueError, "log_level"):
            RuntimeEventSettings(log_level="VERBOSE")
        with self.assertRaisesRegex(ValueError, "지원하지 않는 로그 레벨"):
            _level_from_name("VERBOSE")
        with self.assertRaisesRegex(ValueError, "concurrency"):
            EmbeddingSettings(concurrency=0)

    def test_run_output_rejects_empty_and_dangerous_paths(self) -> None:
        with patch.dict(os.environ, {"ROBO_RUN_OUTPUT_DIR": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "ROBO_RUN_OUTPUT_DIR"):
                AnalysisRunOutputSettings()

        with self.assertRaisesRegex(ValueError, "작업 디렉터리"):
            AnalysisRunOutputSettings(dir=Path("."))

        anchor = Path.cwd().anchor
        if anchor:
            with self.assertRaisesRegex(ValueError, "파일시스템 루트"):
                AnalysisRunOutputSettings(dir=Path(anchor))

    def test_old_runtime_roots_are_absent(self) -> None:
        # logs 는 현행 정본 루트로 복귀 (사용자 지시 2026-07-22) — 옛 루트는 output/run-output.
        for old_root in ("system", "log", "deploy", "output", "run-output"):
            self.assertFalse((REPO / old_root).exists(), old_root)


if __name__ == "__main__":
    unittest.main()
